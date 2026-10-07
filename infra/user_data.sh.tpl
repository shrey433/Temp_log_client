#!/bin/bash
# Runs once when the instance is created. Log: /var/log/rtd-bootstrap.log
exec > >(tee -a /var/log/rtd-bootstrap.log) 2>&1
set -euxo pipefail

dnf install -y docker
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
JSON
systemctl enable --now docker

# ---- data volume: find it by its EBS volume id, format only if it is blank ----
DEV=/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol${volume_id}
for i in $(seq 1 60); do [ -e "$DEV" ] && break; sleep 5; done
[ -e "$DEV" ] || { echo "data volume never appeared"; exit 1; }
if ! blkid "$DEV"; then mkfs.ext4 -L rtd-data "$DEV"; fi
mkdir -p /data
grep -q ' /data ' /etc/fstab || echo "LABEL=rtd-data /data ext4 defaults,nofail 0 2" >> /etc/fstab
mount -a
mkdir -p /data/db /data/caddy

# ---- app bundle and token (instance role grants read on exactly these two things) ----
mkdir -p /opt/rtd/server
aws s3 cp "s3://${bucket}/${key}" /tmp/server.tar.gz --region ${region}
tar -xzf /tmp/server.tar.gz -C /opt/rtd/server
umask 077
echo "RTD_API_TOKEN=$(aws ssm get-parameter --region ${region} --name ${param} --with-decryption --query Parameter.Value --output text)" > /opt/rtd/env
echo "RTD_DB_PATH=/data/rtd.db" >> /opt/rtd/env
umask 022

docker build -t rtd-server /opt/rtd/server
docker network create rtd || true

docker run -d --name rtd-server --network rtd --restart unless-stopped \
  --env-file /opt/rtd/env -v /data/db:/data rtd-server

cat > /opt/rtd/Caddyfile <<CADDY
${host} {
  encode gzip
  reverse_proxy rtd-server:8000
}
CADDY

docker run -d --name caddy --network rtd --restart unless-stopped \
  -p 80:80 -p 443:443 \
  -v /opt/rtd/Caddyfile:/etc/caddy/Caddyfile:ro \
  -v /data/caddy:/data caddy:2

echo "bootstrap finished"
