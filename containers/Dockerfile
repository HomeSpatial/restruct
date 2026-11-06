# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.11.31@sha256:ecd4de2f060c64bea0ff8ecb182ddf46ba3fcccdc8a60cfdbaf20d1a047d7437 AS uv
FROM nvidia/cuda:12.8.1-base-ubuntu24.04@sha256:133c78a0575303be34164d0b90137a042172bdf60696af01a3c424ab402d86e2 AS foundation
ENV DEBIAN_FRONTEND=noninteractive \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# The CUDA packages are already pinned by the base digest; only Ubuntu OS libraries are installed here.
RUN rm -f /etc/apt/sources.list.d/cuda*.list \
 && printf 'APT::Snapshot "20261023T000000Z";\n' > /etc/apt/apt.conf.d/50snapshot \
 && apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
      libgl1 libegl1 libglx0 libxrender1 libxfixes3 libxi6 libxrandr2 libxxf86vm1 \
      libsm6 libice6 libglib2.0-0t64 libxkbcommon0 libgomp1 libfontconfig1 libfreetype6 libusb-1.0-0 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv python install 3.11.15 3.12.12 \
 && uv venv --python 3.12.12 /opt/envs/core \
 && uv venv --python 3.11.15 /opt/envs/spatiallm \
 && uv venv --python 3.12.12 /opt/envs/boxer
FROM foundation AS runtime
COPY locks /opt/locks
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/envs/core/bin/python --require-hashes -r /opt/locks/core.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/envs/spatiallm/bin/python --require-hashes --index-strategy unsafe-best-match \
       --index https://download.pytorch.org/whl/cu124 -r /opt/locks/spatiallm.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/envs/boxer/bin/python --require-hashes --index-strategy unsafe-best-match \
       --index https://download.pytorch.org/whl/cu128 -r /opt/locks/boxer.txt
COPY resources /opt/resources
RUN cd /opt/resources && sha256sum --check SHA256SUMS \
 && mkdir -p /opt/blender /opt/backends/SpatialLM /opt/backends/boxer \
 && tar -xJf blender-5.1.2-linux-x64.tar.xz -C /opt/blender --strip-components=1 \
 && tar -xf spatiallm.tar -C /opt/backends/SpatialLM \
 && tar -xf boxer.tar -C /opt/backends/boxer \
 && rm /opt/resources/*.tar /opt/resources/*.tar.xz
COPY core /opt/recon
COPY addon /opt/addons/homespatial_model
COPY context-files.json /opt/release/context-files.json
RUN uv pip install --python /opt/envs/core/bin/python --no-deps --no-build-isolation /opt/recon \
 && /opt/envs/core/bin/python -c "import indoor_recon, open3d, trimesh" \
 && mkdir -p /opt/release \
 && dpkg-query -W > /opt/release/dpkg-packages.txt \
 && /opt/blender/blender --background --factory-startup --python-expr "import bpy; print('BLENDER_READY',bpy.app.version_string)"
ENV PATH=/opt/envs/core/bin:$PATH \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    XDG_CACHE_HOME=/tmp/recon-cache \
    OMP_NUM_THREADS=6 \
    OPENBLAS_NUM_THREADS=6
WORKDIR /jobs
ENTRYPOINT ["restruct"]
CMD ["--help"]
