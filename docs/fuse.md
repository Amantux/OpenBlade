# FUSE namespace

OpenBlade's FUSE-facing layer is currently a lightweight namespace abstraction over the catalog. It is designed so future kernel FUSE work can reuse the same hydration and metadata contracts without bypassing archive safety rules.
