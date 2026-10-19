#!/usr/bin/env bash
# Creates the on-demand live backend on Azure for Students (DEPLOY.md, part B).
#
# Run it in Azure Cloud Shell (portal.azure.com, the >_ icon, Bash), from a clone of the repository:
# Cloud Shell is already signed in, so no Azure credential passes through this repository.
# Idempotent: safe to run again.
#
#   git clone https://github.com/amaltomajith/Vesalius.git && cd Vesalius
#   bash deploy/azure/provision.sh <duckdns-subdomain> [region]     # region: centralindia
#
# In resource group vesalius-rg it creates:
#   - VM vesalius-vm: Ubuntu 24.04 Arm64, Standard_B4ps_v2 (4 vCPU / 16 GB: a segmentation fits),
#     a 32 GB Standard SSD, a static public IP, inbound 22/80/443 only, a managed identity.
#     It runs only while someone uses it: scripts/deploy/autostop.sh deallocates it when idle.
#   - a custom role "Vesalius VM power" (read, start, deallocate), assigned on that VM only to
#     the VM itself (to put itself to sleep) and to the wake function (to start it).
#   - the wake function (deploy/azure/wake): Flex Consumption, Python 3.11, callable only from
#     the static site (CORS), with a daily cap on wakes.
# It prints the VM's IP (for DuckDNS) and the two values for the GitHub repository variables.
#
# Overrides: VM_SIZE, WAKE_LOCATION (Flex Consumption is not in every region), WAKE_DAILY_CAP,
# SITE_ORIGIN.
set -euo pipefail

SUBDOMAIN="${1:?usage: provision.sh <duckdns-subdomain> [region]}"
SUBDOMAIN="${SUBDOMAIN%.duckdns.org}"
LOCATION="${2:-centralindia}"
WAKE_LOCATION="${WAKE_LOCATION:-$LOCATION}"
SITE_ORIGIN="${SITE_ORIGIN:-https://amaltomajith.github.io}"
SIZE="${VM_SIZE:-Standard_B4ps_v2}"
RG=vesalius-rg
VM=vesalius-vm
ROLE="Vesalius VM power"
HERE="$(cd "$(dirname "$0")" && pwd)"

SUB="$(az account show --query id -o tsv)"
SUFFIX="$(printf '%s' "$SUB" | sha256sum | cut -c1-8)"  # stable per subscription: reruns find them
STORAGE="vesaliuswake$SUFFIX"                          # globally unique, lowercase, <= 24 chars
FUNC="vesalius-wake-$SUFFIX"

echo "== 1/6 resource group $RG ($LOCATION)"
az group create -n "$RG" -l "$LOCATION" -o none

echo "== 2/6 VM $VM ($SIZE, Arm64)"
if ! az vm show -g "$RG" -n "$VM" -o none 2>/dev/null; then
  az vm create -g "$RG" -n "$VM" -l "$LOCATION" \
    --image Canonical:ubuntu-24_04-lts:server-arm64:latest --size "$SIZE" \
    --admin-username azureuser --generate-ssh-keys \
    --os-disk-size-gb 32 --storage-sku StandardSSD_LRS \
    --public-ip-sku Standard --public-ip-address-allocation static \
    --nsg-rule SSH --assign-identity --security-type Standard -o none
fi
az vm open-port -g "$RG" -n "$VM" --port 80,443 --priority 1010 -o none
VM_ID="$(az vm show -g "$RG" -n "$VM" --query id -o tsv)"
VM_PRINCIPAL="$(az vm show -g "$RG" -n "$VM" --query identity.principalId -o tsv)"
IP="$(az vm show -d -g "$RG" -n "$VM" --query publicIps -o tsv)"

echo "== 3/6 role \"$ROLE\": read, start and deallocate, on this VM only"
if [ -z "$(az role definition list --name "$ROLE" --query '[].id' -o tsv)" ]; then
  az role definition create -o none --role-definition "{
    \"Name\": \"$ROLE\",
    \"Description\": \"Read, start and deallocate the Vesalius VM; nothing else.\",
    \"Actions\": [
      \"Microsoft.Compute/virtualMachines/read\",
      \"Microsoft.Compute/virtualMachines/instanceView/read\",
      \"Microsoft.Compute/virtualMachines/start/action\",
      \"Microsoft.Compute/virtualMachines/deallocate/action\"
    ],
    \"AssignableScopes\": [\"/subscriptions/$SUB\"]
  }"
fi
grant() {  # a new role or identity takes a while to reach every region: retry
  for _ in $(seq 1 20); do
    if az role assignment create --assignee-object-id "$1" --assignee-principal-type ServicePrincipal \
      --role "$ROLE" --scope "$VM_ID" -o none 2>/dev/null; then
      return 0
    fi
    sleep 15
  done
  echo "Could not assign \"$ROLE\" to $1 on $VM_ID." >&2
  exit 1
}
grant "$VM_PRINCIPAL"

echo "== 4/6 the wake function's storage ($STORAGE) and app ($FUNC, $WAKE_LOCATION)"
az storage account create -n "$STORAGE" -g "$RG" -l "$WAKE_LOCATION" --sku Standard_LRS \
  --min-tls-version TLS1_2 --allow-blob-public-access false -o none
CONN="$(az storage account show-connection-string -g "$RG" -n "$STORAGE" --query connectionString -o tsv)"
az storage table create -n wakes --connection-string "$CONN" -o none
if ! az functionapp show -g "$RG" -n "$FUNC" -o none 2>/dev/null; then
  az functionapp create -g "$RG" -n "$FUNC" --storage-account "$STORAGE" \
    --flexconsumption-location "$WAKE_LOCATION" --runtime python --runtime-version 3.11 -o none
fi
az functionapp identity assign -g "$RG" -n "$FUNC" -o none
grant "$(az functionapp identity show -g "$RG" -n "$FUNC" --query principalId -o tsv)"

echo "== 5/6 settings, CORS (only $SITE_ORIGIN) and code"
az functionapp config appsettings set -g "$RG" -n "$FUNC" -o none --settings \
  VM_RESOURCE_ID="$VM_ID" BACKEND_URL="https://$SUBDOMAIN.duckdns.org" \
  WAKE_DAILY_CAP="${WAKE_DAILY_CAP:-6}" WAKE_TABLE_CONNECTION="$CONN"
if ! az functionapp cors show -g "$RG" -n "$FUNC" --query allowedOrigins -o tsv | grep -qx "$SITE_ORIGIN"; then
  az functionapp cors add -g "$RG" -n "$FUNC" --allowed-origins "$SITE_ORIGIN" -o none
fi
ZIP="$(mktemp -d)/wake.zip"
(cd "$HERE/wake" && zip -qr "$ZIP" . -x '__pycache__/*')
az functionapp deployment source config-zip -g "$RG" -n "$FUNC" --src "$ZIP" --build-remote true -o none

echo "== 6/6 done"
HOST="$(az functionapp show -g "$RG" -n "$FUNC" --query defaultHostName -o tsv)"
cat <<DONE

VM public IP: $IP
  Point $SUBDOMAIN.duckdns.org at it (duckdns.org), then from this Cloud Shell (it holds the key):
    ssh azureuser@$IP

GitHub repository variables (Settings > Secrets and variables > Actions > Variables):
  WAKE_URL=https://$HOST
  BACKEND_URL=https://$SUBDOMAIN.duckdns.org

Budget: Cost Management > Budgets > Add, \$100 with email alerts at 50% and 80% (DEPLOY.md).
DONE
