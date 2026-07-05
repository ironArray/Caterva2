# Dual-purpose caterva2 peer server image. Build from repo root:
#   container build -f docker/caterva2-server/Dockerfile -t caterva2-peer:latest .
# See plans/remote-peer-containers.md for the full save/load/run recipe.
#
# glibc base (not Alpine/musl): blosc2 and hdf5plugin publish manylinux
# (glibc) wheels but no musllinux ones, so this pulls prebuilt binaries
# instead of compiling from source -- seconds instead of ~6 minutes.
#
# Also machine-capable (systemd + /sbin/init, ~20MB extra): `container
# machine create` boots the image's init system regardless of CMD, while
# plain `container run` uses CMD directly -- so the same image works both
# as a portable single-purpose container and as an interactive dev machine
# with caterva2 already installed.
FROM python:3.14-slim

ENV container container

RUN apt-get update \
 && apt-get install -y --no-install-recommends dbus systemd systemd-sysv \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN > /etc/machine-id
RUN > /var/lib/dbus/machine-id

RUN systemctl set-default multi-user.target
RUN systemctl mask \
      dev-hugepages.mount \
      sys-fs-fuse-connections.mount \
      systemd-update-utmp.service \
      systemd-tmpfiles-setup.service \
      console-getty.service

WORKDIR /opt/caterva2
COPY pyproject.toml README.md ./
COPY caterva2/ ./caterva2/

RUN pip install --no-cache-dir uv \
 && uv pip install --system --no-cache-dir '.[server,hdf5]'

RUN mkdir -p /data/public
COPY docker/caterva2-server/seed.py /tmp/seed.py
RUN python3 /tmp/seed.py && rm /tmp/seed.py
COPY docker/caterva2-server/caterva2-server.toml /data/caterva2-server.toml
# world-writable: plain `container run` executes as root, but `container
# machine run` maps to your non-root host user -- server.py creates
# /data/{shared,personal,media} on startup, so both need write access.
RUN chmod -R a+rwX /data

EXPOSE 8000
CMD ["cat2-server", "--statedir", "/data", "--conf", "/data/caterva2-server.toml"]
