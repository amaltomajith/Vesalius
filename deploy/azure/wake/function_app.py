"""The wake function: the static site's switch for the live backend (DEPLOY.md, part B).

GET  /api/status  the VM's power state, and whether the backend answers /health/ready
POST /api/wake    start the VM if it is asleep (at most WAKE_DAILY_CAP times a UTC day)

Its managed identity may only read and start that one VM (deploy/azure/provision.sh). CORS lets
only the static site call it. It receives no visitor data: both requests have no body.

Settings: VM_RESOURCE_ID, BACKEND_URL, WAKE_DAILY_CAP (default 6), WAKE_TABLE_CONNECTION.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

import azure.functions as func
from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
from azure.data.tables import TableClient, UpdateMode
from azure.identity import DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient
from logic import Decision, decide_status, decide_wake, parse_vm_id, power_of

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

SUB, GROUP, VM = parse_vm_id(os.environ["VM_RESOURCE_ID"])
BACKEND = os.environ["BACKEND_URL"].rstrip("/")
CAP = int(os.environ.get("WAKE_DAILY_CAP", "6"))
compute = ComputeManagementClient(DefaultAzureCredential(), SUB)
wakes = TableClient.from_connection_string(os.environ["WAKE_TABLE_CONNECTION"], table_name="wakes")


def _power() -> str:
    view = compute.virtual_machines.instance_view(GROUP, VM)
    return power_of([s.code or "" for s in view.statuses or []])


def _ready(power: str) -> bool:
    if power != "running":
        return False
    try:
        with urllib.request.urlopen(f"{BACKEND}/health/ready", timeout=3) as r:  # noqa: S310
            return bool(r.status == 200)
    except OSError:
        return False


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _wakes_today() -> int:
    try:
        return int(wakes.get_entity(_today(), "count")["n"])
    except ResourceNotFoundError:
        return 0


def _count_a_wake() -> None:
    """Optimistic concurrency: two simultaneous wakes cannot both read the same count."""
    for _ in range(5):
        try:
            entity = wakes.get_entity(_today(), "count")
            entity["n"] = int(entity["n"]) + 1
            wakes.update_entity(
                entity,
                mode=UpdateMode.REPLACE,
                etag=entity.metadata["etag"],
                match_condition=MatchConditions.IfNotModified,
            )
            return
        except ResourceNotFoundError:
            try:
                wakes.create_entity({"PartitionKey": _today(), "RowKey": "count", "n": 1})
                return
            except ResourceExistsError:
                continue
        except ResourceModifiedError:
            continue


def _answer(d: Decision) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(d.body),
        status_code=d.status,
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.route(route="status", methods=["GET"])
def status(req: func.HttpRequest) -> func.HttpResponse:
    power = _power()
    return _answer(decide_status(power, _ready(power), _wakes_today(), CAP))


@app.route(route="wake", methods=["POST"])
def wake(req: func.HttpRequest) -> func.HttpResponse:
    power = _power()
    decision = decide_wake(power, _ready(power), _wakes_today(), CAP)
    if decision.start:
        _count_a_wake()
        compute.virtual_machines.begin_start(GROUP, VM)  # not awaited: the site polls /api/status
    return _answer(decision)
