# Local development hardware observation — 2026-10-25

Observed from this development host (not a customer capacity claim):

- AMD Ryzen 3 5300U, four physical cores / eight logical CPUs.
- Approximately 7.1 GiB usable RAM and 2 GiB swap.
- At observation, roughly 0.5–0.9 GiB RAM was available and swap was nearly full;
  this includes other applications, not just this project.
- No NVIDIA Docker runtime was registered; no NVIDIA command was available.
- This project's five idle application containers reported approximately 119 MiB
  combined working memory in one `docker stats --no-stream` sample, excluding
  build/test/restore jobs and without a model or representative workload.

No generation model was installed or downgraded to fit this machine. A reliable AI
quality/latency benchmark cannot be claimed here without a suitable target model
and sufficient available resources. The architecture's 32–64 GB / 12 GB VRAM lab
profile remains an unverified test target. CPU-only experiments may be evaluated
separately; they must not silently become the production configuration.

The application is useful for documents/reviews/text search while AI is pending.
