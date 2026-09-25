FROM quay.io/minio/mc:latest

COPY create-buckets.sh /create-buckets.sh

ENTRYPOINT ["/bin/sh", "/create-buckets.sh"]
