# The NFS server role needs a Ganesha built with VFS POSIX-ACL mapping so NFSv4
# ACLs (nfs4_setfacl) work; Debian bookworm's nfs-ganesha 4.3 is built without it.
# Fedora 41's nfs-ganesha 5.9 links libacl into libfsalvfs.so and exports
# fsal_acl_2_posix_acl. fedora:41 pinned by digest (docker inspect RepoDigests).
FROM fedora@sha256:f1a3fab47bcb3c3ddf3135d5ee7ba8b7b25f2e809a47440936212a3a50957f3d AS nfs-ganesha
RUN dnf -y install --setopt=install_weak_deps=False nfs-ganesha nfs-ganesha-vfs rpcbind \
 && dnf clean all
COPY hydrator_shim.py /usr/local/bin/hydrator_shim.py

# samba and client roles: one pinned Debian base, the role chosen by build arg.
# debian:bookworm-slim pinned by digest (docker inspect RepoDigests).
# Last stage, so it stays the default build target.
FROM debian@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587 AS debian-roles
ARG ROLE
RUN apt-get update \
 && if [ "$ROLE" = samba ]; then pkgs="samba smbclient"; \
    else pkgs="smbclient cifs-utils nfs-common nfs4-acl-tools python3 python3-pip attr procps"; fi \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $pkgs \
 && apt-get clean \
 && if [ "$ROLE" = client ]; then pip3 install --break-system-packages --no-cache-dir smbprotocol==1.15.0; fi
COPY hydrator_shim.py /usr/local/bin/hydrator_shim.py
