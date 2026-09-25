#!/bin/sh
# One-shot: creates the four zone buckets, each with its own scoped access key limited
# to that single bucket. Bronze's isolation (separate bucket, separate credentials) is
# explicit in STAGES.md; silver/gold/quarantine follow the same pattern for a uniform
# credential boundary.
set -e

if [ -n "$MINIO_ROOT_PASSWORD_FILE" ] && [ -f "$MINIO_ROOT_PASSWORD_FILE" ]; then
    MINIO_ROOT_PASSWORD="$(cat "$MINIO_ROOT_PASSWORD_FILE")"
fi

mc alias set local "http://minio:9000" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"

create_zone() {
    zone="$1"
    access_key_var="MINIO_${zone}_ACCESS_KEY"
    secret_key_var="MINIO_${zone}_SECRET_KEY"
    eval "access_key=\$$access_key_var"
    eval "secret_key=\$$secret_key_var"

    bucket=$(echo "$zone" | tr '[:upper:]' '[:lower:]')
    mc mb --ignore-existing "local/$bucket"

    policy_file="/tmp/${bucket}-rw.json"
    cat > "$policy_file" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::${bucket}", "arn:aws:s3:::${bucket}/*"]
    }
  ]
}
EOF

    mc admin user add local "$access_key" "$secret_key" || true
    mc admin policy create local "${bucket}-rw" "$policy_file" || true
    mc admin policy attach local "${bucket}-rw" --user "$access_key" || true
}

create_zone BRONZE
create_zone SILVER
create_zone GOLD
create_zone QUARANTINE

echo "minio-init: buckets and credentials ready"
