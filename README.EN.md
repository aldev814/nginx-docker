# nginx-docker

[![Test and Publish](https://github.com/aldev814/nginx-docker/actions/workflows/test_and_publish.yml/badge.svg)](https://github.com/aldev814/nginx-docker/actions/workflows/test_and_publish.yml)

[中文](README.md) | [English](README.EN.md)

A custom Alpine-based Nginx Docker image for websites, reverse proxies, and TCP routing. This project is based on [InsulateJustf/docker-nginx-http2-brotli](https://github.com/InsulateJustf/docker-nginx-http2-brotli).

## Current build configuration

This describes the repository source. Published images may lag behind source changes. See the [Dockerfile](Dockerfile) and run `nginx -V` in the actual image to check versions and build options.

| Component | Configuration |
| --- | --- |
| Nginx | 1.31.8, built from official sources |
| Base image | Alpine 3.23, multi-stage build |
| OpenSSL | Alpine development and runtime packages; no custom source build. Version follows the package repositories |
| gzip | Built with Cloudflare zlib sources; gzip enabled by default |
| Brotli | ngx_brotli compiled in; `brotli` and `brotli_static` enabled by default |
| Response headers | headers-more 0.40, used by the default header configuration |
| Regular expressions | PCRE2 10.47 with JIT support; configure `pcre_jit on` to enable JIT |
| Memory allocator | System allocator; the mimalloc build recipe is retained as comments |

The conditional jemalloc build recipe is also retained. Building an allocator alone does not replace Nginx's allocator. Neither mimalloc nor jemalloc is integrated through linking or `LD_PRELOAD`; changing `MALLOC_IMPL` does not activate a runtime replacement.

### Modules and patches

| Feature | Status |
| --- | --- |
| HTTPS, HTTP/2, HTTP/3 | Compiled in; require site configuration. HTTP/3 also requires an accessible, published UDP port |
| stream, stream SSL, SNI preread, HTTP/stream realip | Compiled in |
| addition, sub, DAV, MP4, gunzip, gzip_static, random_index, secure_link, stub_status, auth_request, slice | Compiled in; use depends on configuration |
| Threads, file AIO, dynamic module compatibility | Compiled in |
| fancyindex, substitutions filter, GeoIP2, HTTP-FLV | Sources still downloaded; modules not compiled |
| Image filter, XSLT, official GeoIP, official FLV, mail | Not enabled at build time |
| SCGI | Explicitly disabled |
| Dynamic TLS record patch | kn007's `nginx_dynamic_tls_records.patch` retained. Dynamic records are disabled by default; configure `ssl_dyn_rec_enable on` to enable them. The patch also raises the serialized TLS session size limit to 16K |
| OpenSSL MD5/SHA1 patch, libatomic_ops | Removed; use Nginx's native hash and atomic operation implementations |

Cloudflare zlib, ngx_brotli, HTTP-FLV, and substitutions filter are fetched from upstream repositories without pinning every commit. Builds from the same Dockerfile may differ over time.

## Build and run

Docker must support Linux containers. Alpine is the container environment; the host can run Debian or another Linux distribution.

```sh
docker build -t aldev814/nginx-docker:local .
docker run --rm aldev814/nginx-docker:local nginx -V
```

The image does not ship a default website. Create a simple HTTP site:

```sh
mkdir -p public
printf '<p>It works!</p>\n' > public/index.html
cat > site.conf <<'CONF'
server {
    listen 80;
    server_name _;

    location / {
        root /usr/share/nginx/html;
        index index.html;
    }
}
CONF

docker run -d --name nginx \
  -p 8080:80 \
  -v "$PWD/public:/usr/share/nginx/html:ro" \
  -v "$PWD/site.conf:/etc/nginx/conf.d/site.conf:ro" \
  aldev814/nginx-docker:local

curl http://localhost:8080
docker logs nginx
```

The release workflow targets `aldev814/nginx-docker` and `ghcr.io/aldev814/nginx-docker` (the Docker Hub username comes from a repository secret; the GHCR namespace follows the triggering actor). To use a published image, select a successfully published tag, for example:

```sh
docker pull ghcr.io/aldev814/nginx-docker:latest
```

## Configuration mounts

| Path | Purpose |
| --- | --- |
| `/etc/nginx/nginx.conf` | Main configuration; mount a file here to replace the image default |
| `/etc/nginx/main.d/*.conf` | Included in the main context by the default configuration; suitable for `env`, `load_module`, and `stream` |
| `/etc/nginx/conf.d/*.conf` | Included in the http context by the default configuration; suitable for site configuration |
| `/etc/nginx/conf.d/ssl_common.conf` | Bundled shared TLS settings, included in the http context by default |
| `/etc/ssl/dhparam.pem` | Mozilla FFDHE 2048 parameters downloaded during the build |

Mounting an entire `conf.d` directory hides the bundled `ssl_common.conf`. If replacing `nginx.conf`, retain the includes you need.

The shared TLS configuration permits TLS 1.2/1.3, configures a session cache, disables session tickets, and sets cipher and OCSP stapling options. HTTPS sites still need certificates and private keys. Compiling HTTP/3 support does not enable it automatically; CI currently does not test HTTP/3.

The default configuration also clears selected response headers, adds headers including CSP, and sets `worker_processes 1`. Review the header policies and worker count for your application. Nginx runs in the foreground, with default access/error logs sent to stdout/stderr.

## Dependency updates, tests, and releases

- **Update Dependencies** runs daily at 00:00 UTC (08:00 China Standard Time) and on manual dispatch. Version changes are committed to the Dockerfile with a release marker requesting a build and release. Version fields for disabled modules and allocators may still be updated.
- **Test and Publish** builds on manual dispatch or when a main-branch commit message contains `[RELEASE_TAG:...]`. A normal push triggers the workflow but skips the build job.
- Tests cover container startup, an HTTP/1.1 Brotli response, and an HTTPS/HTTP/2 response. The image build also runs `nginx -t`.
- Leaving manual `release_tag` empty performs tests only if the current commit has no release marker. Supplying a valid tag, such as `v1.31.8`, or using a commit release marker publishes images and creates a Git tag after successful tests.
- Published image tags include `latest`, the full version without the leading `v`, and the major/minor version. Configure `GH_PAT`, `DOCKER_USERNAME`, and `DOCKER_TOKEN`, and grant `GITHUB_TOKEN` the required package write permissions.

## Acknowledgements

- [InsulateJustf/docker-nginx-http2-brotli](https://github.com/InsulateJustf/docker-nginx-http2-brotli): the upstream project this repository is based on.
- [kn007/patch](https://github.com/kn007/patch): the Nginx dynamic TLS record patch.
- [Cloudflare zlib](https://github.com/cloudflare/zlib), [ngx_brotli](https://github.com/google/ngx_brotli), and [headers-more](https://github.com/openresty/headers-more-nginx-module): compression and header modules.
- [akafeng/docker-nginx](https://github.com/akafeng/docker-nginx) and [macbre/docker-nginx-http3](https://github.com/macbre/docker-nginx-http3): reference projects acknowledged by upstream.
