# For maintainers

## Refreshing the license-validated `influxdb-data` volume artifact

Smoke test (Tier 3) requires a license-validated `influxdb-data` volume so CI doesn't have
to click a validation email on every run. Refresh process:

1. Locally: `make clean && INFLUXDB3_ENTERPRISE_EMAIL=ci+iiot@example.com make up`
2. Click the validation link sent to that mailbox.
3. Wait until `make ps` shows `influxdb3` as `healthy`.
4. `make down` (preserves the volume).
5. Export the volume contents:
   ```bash
   docker run --rm -v influxdb3-ref-iiot_influxdb-data:/data -v "$PWD":/out busybox \
     sh -c "tar czf /out/influxdb-data.tar.gz -C /data ."
   ```
6. Upload `influxdb-data.tar.gz` as a GitHub Actions artifact named `influxdb-data-validated`
   via the manual workflow dispatch on `.github/workflows/refresh-volume.yml` (set up later).

Refresh cadence: monthly, or when the license terms change.

## Common gotchas

- Health endpoint requires auth; use `curl -s -o /dev/null` (any HTTP response means up).
- Cron strings are 6-field. See `ARCHITECTURE.md` § "Plugin conventions".
- `LineBuilder` is injected by the engine, not imported. Same place.
