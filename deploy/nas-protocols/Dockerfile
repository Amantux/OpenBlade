# One pinned base for all three rig roles; the role is chosen by build arg.
# debian:bookworm-slim pinned by digest (docker inspect RepoDigests).
FROM debian@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587
ARG ROLE
RUN apt-get update \
 && if [ "$ROLE" = samba ]; then pkgs="samba smbclient"; \
    elif [ "$ROLE" = nfs ]; then pkgs="nfs-ganesha nfs-ganesha-vfs rpcbind dbus"; \
    else pkgs="smbclient cifs-utils nfs-common nfs4-acl-tools python3 python3-pip attr procps"; fi \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $pkgs \
 && apt-get clean \
 && if [ "$ROLE" = client ]; then pip3 install --break-system-packages --no-cache-dir smbprotocol==1.15.0; fi
COPY hydrator_shim.py /usr/local/bin/hydrator_shim.py
