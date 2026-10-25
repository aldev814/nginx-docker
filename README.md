# nginx-docker

[![Test and Publish](https://github.com/aldev814/nginx-docker/actions/workflows/test_and_publish.yml/badge.svg)](https://github.com/aldev814/nginx-docker/actions/workflows/test_and_publish.yml)

[中文](README.md) | [English](README.EN.md)

基于 Alpine 的自定义 Nginx Docker 镜像，用于网站托管、反向代理和 TCP 分流。本项目基于 [InsulateJustf/docker-nginx-http2-brotli](https://github.com/InsulateJustf/docker-nginx-http2-brotli) 维护和调整。

## 当前构建配置

以下描述对应仓库源码；已发布镜像可能晚于源码更新。版本和编译参数以 [Dockerfile](Dockerfile) 及实际镜像的 `nginx -V` 为准。

| 组件 | 当前配置 |
| --- | --- |
| Nginx | 1.31.8，从官方源码编译 |
| 基础镜像 | Alpine 3.23，多阶段构建 |
| OpenSSL | 使用 Alpine 的开发包和运行库，不再自行下载、编译；版本随软件源变化 |
| gzip | 使用 Cloudflare zlib 源码构建；默认开启 gzip |
| Brotli | 编译 ngx_brotli；默认开启 `brotli`、`brotli_static` |
| 响应头控制 | headers-more 0.40，默认配置使用其设置和清理响应头 |
| 正则表达式 | PCRE2 10.47，编译 JIT 支持；使用时需配置 `pcre_jit on` |
| 内存分配器 | 使用系统分配器；mimalloc 构建代码已注释保留 |

仍保留 jemalloc 的条件构建代码，但单独构建该库不会替换 Nginx 的分配器。当前没有通过链接或 `LD_PRELOAD` 接入 mimalloc/jemalloc；修改 `MALLOC_IMPL` 不代表运行时替换已生效。

### 模块和补丁

| 项目 | 当前状态 |
| --- | --- |
| HTTPS、HTTP/2、HTTP/3 | 已编译支持；需在站点配置中启用。HTTP/3 还需开放并映射 UDP 端口 |
| stream、stream SSL、SNI 预读、HTTP/stream realip | 已编译支持 |
| addition、sub、DAV、MP4、gunzip、gzip_static、random_index、secure_link、stub_status、auth_request、slice | 已编译支持，是否生效取决于配置 |
| threads、file AIO、动态模块兼容支持 | 已编译支持 |
| fancyindex、substitutions filter、GeoIP2、HTTP-FLV | 保留源码下载，不编译 |
| 图片处理、XSLT、官方 GeoIP、官方 FLV、mail | 未启用编译 |
| SCGI | 显式禁用 |
| 动态 TLS record 补丁 | 保留 kn007 的 `nginx_dynamic_tls_records.patch`；动态 record 功能默认关闭，需配置 `ssl_dyn_rec_enable on`；补丁另将 TLS session 序列化大小上限提高到 16K |
| OpenSSL MD5/SHA1 补丁、libatomic_ops | 已移除，使用 Nginx 原生哈希及原子操作实现 |

Cloudflare zlib、ngx_brotli、HTTP-FLV 和 substitutions filter 从上游仓库获取源码，未全部固定到提交；相同 Dockerfile 在不同日期构建的内容可能不同。

## 构建与运行

需要能够运行 Linux 容器的 Docker。容器内部使用 Alpine，并不限制宿主机发行版，可在 Debian 等 Linux 系统运行。

```sh
docker build -t aldev814/nginx-docker:local .
docker run --rm aldev814/nginx-docker:local nginx -V
```

镜像不附带默认站点页面。以下例子创建一个 HTTP 站点：

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

发布工作流的目标镜像为 `aldev814/nginx-docker` 和 `ghcr.io/aldev814/nginx-docker`（Docker Hub 用户名由仓库 Secret 决定，GHCR 使用仓库所有者命名空间）。使用已发布镜像时，选择已完成发布的标签，例如：

```sh
docker pull ghcr.io/aldev814/nginx-docker:latest
```

## 配置挂载

| 路径 | 用途 |
| --- | --- |
| `/etc/nginx/nginx.conf` | 主配置；挂载文件可替换镜像默认配置 |
| `/etc/nginx/main.d/*.conf` | 默认主配置在 main 上下文包含这些文件，可放 `env`、`load_module`、`stream` 等配置 |
| `/etc/nginx/conf.d/*.conf` | 默认主配置在 http 上下文包含这些文件，可放站点配置 |
| `/etc/nginx/conf.d/ssl_common.conf` | 镜像提供的 TLS 通用配置，默认包含于 http 上下文 |
| `/etc/ssl/dhparam.pem` | 构建时获取的 Mozilla FFDHE 2048 参数 |

挂载整个 `conf.d` 目录会遮盖镜像内的 `ssl_common.conf`；替换 `nginx.conf` 后也需自行保留需要的 include。

TLS 通用配置限定 TLS 1.2/1.3，并配置 session cache、关闭 session tickets、设置密码套件及 OCSP stapling。HTTPS 站点仍需自行配置证书和私钥。HTTP/3 不会因为编译支持而自动启用，当前 CI 未验证 HTTP/3。

默认主配置还会清理部分响应头、设置 CSP 等响应头，并使用 `worker_processes 1`。部署前请按应用检查响应头策略和 worker 数量。容器默认以前台 Nginx 进程运行，将默认访问、错误日志输出至 stdout/stderr。

## 自动更新、测试与发布

两个工作流均运行于 GitHub Actions 的 Ubuntu 24.04 runner，使用 Bash 严格错误处理、超时和并发控制。Actions 固定到已核对的发布提交。

- **Update Dependencies**：每天 UTC 00:00（北京时间 08:00）及手动触发，只允许从 main 运行。更新 Nginx、headers-more、PCRE2，并创建依赖更新 PR，不直接修改 main。
- Nginx 自动更新保持当前主次版本分支，手动指定完整版本或主次版本可调整目标；显式降级需要勾选 `allow_downgrade`。备用分配器及未编译模块不再参与自动更新。
- 自动更新 PR 的提交和标题携带唯一的 `[RELEASE_TAG:v版本-upd运行ID]` 标记。应在 PR 测试通过后合并，并在 merge/squash 提交中保留该标记，才会请求自动发布。建议将构建检查设为 main 的必需检查。
- **Test and Publish**：main 的普通 push、PR 及手动运行都会构建测试。PR 只有只读权限，不获取发布凭据；手动 `release_tag` 留空始终只测试，不读取提交中的发布标记。
- 测试沿用仓库的静态站点配置，检查 HTTP/1.1 状态码、Brotli、响应正文、Server 头缺失、HTTPS/HTTP/2 及 TLS 1.2/1.3。启动采用有上限的就绪轮询，失败时上传诊断日志，并始终清理容器。
- 发布仅允许 main 当前最新提交。手动标签须与 Dockerfile 的 Nginx 版本一致，例如 `v1.31.8-upd1`。已有 Git tag 一律拒绝覆盖；发布阶段串行执行并再次检查 main，避免旧构建更新 `latest`。
- 镜像只构建一次。测试通过后将镜像保存为临时 artifact；独立发布 job 校验镜像 ID 和源码 revision，再推送同一镜像，不二次构建。
- 发布先保留 Git tag，再推送完整版本标签，最后更新主次版本及 `latest`。Git 与两个注册表不支持跨服务原子事务；若中途失败，可能留下已保留的标签或部分镜像。应检查已有发布，并用新的唯一标签重试，不覆盖旧标签。

GHCR 镜像地址固定为 `ghcr.io/<仓库所有者>/<仓库名>`，Docker Hub 为 `<DOCKER_USERNAME>/<仓库名>`，不随触发者变化。当前仓库的 GHCR 地址为 `ghcr.io/aldev814/nginx-docker`。

自动更新使用仓库自带的 `GITHUB_TOKEN` 创建分支和 PR，身份为 `github-actions[bot]`，不再使用个人 `GH_PAT`。更新工作流声明 `contents: write`、`pull-requests: write`；仓库 Settings → Actions → General → Workflow permissions 需允许 GitHub Actions 创建 PR。由该 Token 创建或更新的 PR，其 CI 按 GitHub 规则可能需要点击 **Approve workflows to run** 后运行。发布需要 `DOCKER_USERNAME`、`DOCKER_TOKEN`，发布 job 为 `GITHUB_TOKEN` 显式声明 `contents: write` 和 `packages: write`。仓库/组织策略也须允许相应操作。

工作流逻辑的回归检查可单独运行，无需 Docker：

```sh
python3 -m unittest discover -s .github/scripts -p 'test_*.py' -v
```

实际镜像构建和 HTTP/TLS 集成测试由 GitHub Actions 执行；当前不包含 HTTP/3 或业务代理测试。

## 致谢

- [InsulateJustf/docker-nginx-http2-brotli](https://github.com/InsulateJustf/docker-nginx-http2-brotli)：本项目的上游仓库。
- [kn007/patch](https://github.com/kn007/patch)：Nginx 动态 TLS record 补丁。
- [Cloudflare zlib](https://github.com/cloudflare/zlib)、[ngx_brotli](https://github.com/google/ngx_brotli)、[headers-more](https://github.com/openresty/headers-more-nginx-module)：压缩与响应头模块。
- [akafeng/docker-nginx](https://github.com/akafeng/docker-nginx)、[macbre/docker-nginx-http3](https://github.com/macbre/docker-nginx-http3)：上游保留的参考项目。
