#!/usr/bin/env bash
# Create the staging VM on pve2. Run ON pve2, as root:
#
#   scp deploy/homelab/{create-vm.sh,cloud-init.yaml} root@pve2:/root/
#   ssh root@pve2 'bash /root/create-vm.sh'
#
# Refuses to touch an existing VMID. To rebuild from scratch:
#   qm stop 110 && qm destroy 110 --purge
#
# Every limit below exists so staging cannot crowd the other guests on the
# host — it is the one VM on pve2 that should give way. See README.md.
set -euo pipefail

VMID=${VMID:-110}
NAME=eesoc-staging
STORAGE=local-lvm
DISK_GB=32
IMG_URL=https://cloud.debian.org/images/cloud/trixie/latest
IMG=debian-13-genericcloud-amd64.qcow2
HERE=$(cd "$(dirname "$0")" && pwd)

[[ $(hostname) == pve2 ]] || { echo "run this on pve2" >&2; exit 1; }
if qm status "$VMID" >/dev/null 2>&1; then
  echo "VM $VMID already exists; refusing to touch it" >&2; exit 1
fi

# Raph's Mac (the key the other homelab VMs carry) and pve2's own root key.
KEYS=$(grep -E 'raphsinai@MacBookPro|root@pve2$' /root/.ssh/authorized_keys \
       | sed 's/^/      - /')
[[ -n $KEYS ]] || { echo "no SSH keys found to authorise" >&2; exit 1; }

mkdir -p /var/lib/vz/snippets /var/lib/vz/import
while IFS= read -r line; do
  if [[ $line == *__SSH_AUTHORIZED_KEYS__* ]]; then printf '%s\n' "$KEYS"
  else printf '%s\n' "$line"; fi
done < "$HERE/cloud-init.yaml" > "/var/lib/vz/snippets/$NAME-user.yaml"

cd /var/lib/vz/import
if [[ ! -f $IMG ]]; then
  curl -fsSLO "$IMG_URL/SHA512SUMS"
  curl -fsSLO "$IMG_URL/$IMG"
fi
grep " $IMG\$" SHA512SUMS | sha512sum -c -

# memory/balloon: 6 GiB, of which up to 2 GiB is handed back when pve2 runs
#   short (Proxmox inflates balloons above ~80% host RAM). Never in HA: if
#   pve2 dies, HA would restart staging on pve, whose RAM belongs to the NAS.
# cpuunits 50: half the default weight, so under contention every other VM
#   on the host wins. cpulimit 2 caps it at two cores' worth of time, half
#   the host, spread over four vCPUs: RKE2's control plane alone requests
#   more than two cores' worth of scheduling room, so with only two vCPUs
#   CoreDNS could not be placed at all.
# disk: iothread + bandwidth/IOPS caps so an image pull or a restore cannot
#   starve the SSD; discard so trimmed blocks go back to the shared thin pool.
# net: 25 MB/s cap so image pulls cannot saturate the home uplink.
# startup: last, a minute after everything else.
qm create "$VMID" \
  --name "$NAME" \
  --description "EESoc website staging (RKE2). deploy/homelab/README.md in the eesoc-website repo. Disposable: rebuilt from that directory." \
  --tags "eesoc;staging" \
  --ostype l26 --machine q35 --bios seabios \
  --cpu x86-64-v3 --cores 4 --sockets 1 --cpulimit 2 --cpuunits 50 \
  --memory 6144 --balloon 4096 \
  --scsihw virtio-scsi-single \
  --net0 virtio,bridge=vmbr0,rate=25 \
  --serial0 socket --vga serial0 \
  --agent enabled=1,fstrim_cloned_disks=1 \
  --onboot 1 --startup order=99,up=60

qm set "$VMID" --scsi0 "$STORAGE:0,import-from=/var/lib/vz/import/$IMG,discard=on,ssd=1,iothread=1,mbps_rd=200,mbps_wr=100,iops_rd=4000,iops_wr=2000"
qm disk resize "$VMID" scsi0 "${DISK_GB}G"
qm set "$VMID" --ide2 "$STORAGE:cloudinit" --boot order=scsi0
qm set "$VMID" --ipconfig0 ip=dhcp --nameserver "1.1.1.1 9.9.9.9" \
  --cicustom "user=local:snippets/$NAME-user.yaml"

# The image has been copied into the thin pool; the download only takes space
# on pve2's root filesystem.
rm -f "/var/lib/vz/import/$IMG" /var/lib/vz/import/SHA512SUMS

qm start "$VMID"
echo "started $VMID; IP once the guest agent is up:"
echo "  qm guest cmd $VMID network-get-interfaces"
