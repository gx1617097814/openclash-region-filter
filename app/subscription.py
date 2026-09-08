from __future__ import annotations

import dataclasses
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .filtering import (
    config_with_dynamic_regions,
    dump_yaml,
    filter_config_data,
    load_yaml_text,
)


@dataclasses.dataclass
class SubscriptionResult:
    ok: bool
    changed: bool
    source_url: str
    cache_path: str
    generated_at: str | None
    regions_added: list[str]
    filter_result: dict[str, Any] | None
    subscription_info: dict[str, Any] | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class InstallResult:
    ok: bool
    changed: bool
    source_path: str
    output_config_path: str
    installed_at: str | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def format_bytes(value: int | None) -> str:
    if value is None:
        return "未知"
    if value == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    amount = float(value)
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    if unit == "B":
        return f"{int(amount)} {unit}"
    return f"{amount:.1f} {unit}"


def parse_subscription_userinfo(header: str) -> dict[str, Any]:
    values: dict[str, int] = {}
    for key in ("upload", "download", "total", "expire"):
        match = re.search(rf"(?:^|[;\s]){key}=(\d+)", header, re.IGNORECASE)
        if match:
            values[key] = int(match.group(1))

    upload = values.get("upload")
    download = values.get("download")
    total = values.get("total")
    expire = values.get("expire")
    used = (upload or 0) + (download or 0) if upload is not None or download is not None else None
    remaining = max(total - used, 0) if total is not None and used is not None and total > 0 else None
    percent_remaining = (
        round((remaining / total) * 100, 1)
        if remaining is not None and total
        else None
    )

    if expire == 0:
        expire_text = "长期有效"
        days_left: int | str | None = "∞"
    elif expire:
        expire_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(expire))
        days_left = max(0, int((expire - time.time() + 86399) // 86400))
    else:
        expire_text = "未知"
        days_left = None

    return {
        "raw": header,
        "upload": upload,
        "download": download,
        "total": total,
        "used": used,
        "remaining": remaining,
        "percent_remaining": percent_remaining,
        "expire": expire,
        "expire_text": expire_text,
        "days_left": days_left,
        "upload_text": format_bytes(upload),
        "download_text": format_bytes(download),
        "used_text": format_bytes(used),
        "total_text": "∞" if total == 0 else format_bytes(total),
        "remaining_text": "∞" if total == 0 else format_bytes(remaining),
    }


def _openclash_proxy_opener(config: dict[str, Any]) -> urllib.request.OpenerDirector | None:
    openclash = config.get("openclash", {})
    paths = [
        str(openclash.get("runtime_config_path") or ""),
        "/etc/openclash/openclash-region-filter.yaml",
    ]
    runtime: dict[str, Any] = {}
    for value in dict.fromkeys(paths):
        if not value:
            continue
        try:
            runtime = load_yaml_text(Path(value).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if runtime:
            break

    port = runtime.get("mixed-port") or runtime.get("port")
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if not 1 <= port <= 65535:
        return None

    proxy_url = f"http://127.0.0.1:{port}"
    handlers: list[Any] = [urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})]
    authentication = runtime.get("authentication") or []
    if isinstance(authentication, str):
        authentication = [authentication]
    if isinstance(authentication, list) and authentication:
        username, separator, password = str(authentication[0]).partition(":")
        if separator and username:
            manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            manager.add_password(None, proxy_url, username, password)
            handlers.append(urllib.request.ProxyBasicAuthHandler(manager))
    return urllib.request.build_opener(*handlers)


def _open_subscription(
    request: urllib.request.Request,
    timeout: int,
    config: dict[str, Any] | None = None,
) -> Any:
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except (OSError, urllib.error.URLError, TimeoutError) as direct_error:
        opener = _openclash_proxy_opener(config or {})
        if opener is None:
            raise
        proxy_error: Exception | None = None
        for attempt in range(2):
            try:
                return opener.open(request, timeout=timeout)
            except urllib.error.HTTPError:
                raise
            except (OSError, urllib.error.URLError, TimeoutError) as exc:
                proxy_error = exc
                if attempt == 0:
                    time.sleep(0.25)
        assert proxy_error is not None
        raise proxy_error from direct_error


def _network_error_message() -> str:
    return "订阅连接失败；已尝试直连和 OpenClash 本机代理，请稍后重试"


def fetch_subscription_info(config: dict[str, Any]) -> dict[str, Any]:
    subscription = config.get("subscription", {})
    source_url = str(subscription.get("source_url", "")).strip()
    timeout = max(1, int(subscription.get("timeout_seconds", 30)))
    user_agent = str(subscription.get("user_agent") or "clash.meta")

    if not source_url:
        return {"ok": False, "error": "subscription.source_url is empty"}

    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": user_agent},
        method="GET",
    )
    try:
        with _open_subscription(request, timeout, config) as response:
            header = response.headers.get("subscription-userinfo") or ""
            if not header:
                return {"ok": False, "error": "subscription-userinfo header is missing"}
            info = parse_subscription_userinfo(header)
            info.update({"ok": True, "status": response.status, "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")})
            return info
    except (OSError, urllib.error.URLError, TimeoutError):
        return {"ok": False, "error": _network_error_message()}


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def fetch_subscription_yaml(source_url: str, timeout: int, user_agent: str, config: dict[str, Any] | None = None) -> str:
    text, _, _ = fetch_subscription_document(source_url, timeout, user_agent, config)
    return text


def fetch_subscription_document(
    source_url: str,
    timeout: int,
    user_agent: str,
    config: dict[str, Any] | None = None,
) -> tuple[str, str, int]:
    request = urllib.request.Request(
        source_url,
        headers={
            "User-Agent": user_agent or "clash.meta",
            "Accept": "application/x-yaml,text/yaml,text/plain,*/*",
        },
    )
    with _open_subscription(request, timeout, config) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return (
            response.read().decode(charset, errors="replace"),
            response.headers.get("subscription-userinfo") or "",
            response.status,
        )


def refresh_subscription(config: dict[str, Any]) -> tuple[dict[str, Any], SubscriptionResult]:
    subscription = config.get("subscription", {})
    source_url = str(subscription.get("source_url", "")).strip()
    cache_path = Path(str(subscription.get("cache_path") or "/data/subscription-filtered.yaml"))
    source_path = Path(str(subscription.get("last_source_path") or "/data/subscription-source.yaml"))
    timeout = max(1, int(subscription.get("timeout_seconds", 30)))
    user_agent = str(subscription.get("user_agent") or "clash.meta")

    if not source_url:
        return config, SubscriptionResult(
            ok=False,
            changed=False,
            source_url="",
            cache_path=str(cache_path),
            generated_at=None,
            regions_added=[],
            filter_result=None,
            subscription_info=None,
            error="subscription.source_url is empty",
        )

    try:
        source_text, userinfo_header, response_status = fetch_subscription_document(
            source_url,
            timeout,
            user_agent,
            config,
        )
        data = load_yaml_text(source_text)
        prepared_config = config_with_dynamic_regions(data, config)
        before_region_ids = {str(region.get("id")) for region in config.get("regions", [])}
        after_region_ids = {str(region.get("id")) for region in prepared_config.get("regions", [])}
        regions_added = sorted(after_region_ids - before_region_ids)

        filtered, filter_result = filter_config_data(data, prepared_config)
        rendered = dump_yaml(filtered)

        old_rendered = cache_path.read_text(encoding="utf-8") if cache_path.exists() else None
        atomic_write(cache_path, rendered)
        atomic_write(source_path, source_text)

        updated_config = dict(config)
        updated_config["regions"] = prepared_config["regions"]
        filter_payload = filter_result.to_dict()
        filter_payload["config_path"] = str(source_path)
        subscription_info = None
        if userinfo_header:
            subscription_info = parse_subscription_userinfo(userinfo_header)
            subscription_info.pop("raw", None)
            subscription_info.update({
                "ok": True,
                "status": response_status,
                "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })

        return updated_config, SubscriptionResult(
            ok=True,
            changed=old_rendered != rendered,
            source_url=source_url,
            cache_path=str(cache_path),
            generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            regions_added=regions_added,
            filter_result=filter_payload,
            subscription_info=subscription_info,
        )
    except (OSError, urllib.error.URLError, TimeoutError):
        return config, SubscriptionResult(
            ok=False,
            changed=False,
            source_url=source_url,
            cache_path=str(cache_path),
            generated_at=None,
            regions_added=[],
            filter_result=None,
            subscription_info=None,
            error=_network_error_message(),
        )
    except ValueError as exc:
        return config, SubscriptionResult(
            ok=False,
            changed=False,
            source_url=source_url,
            cache_path=str(cache_path),
            generated_at=None,
            regions_added=[],
            filter_result=None,
            subscription_info=None,
            error=str(exc),
        )


def refilter_cached_subscription(config: dict[str, Any]) -> tuple[dict[str, Any], SubscriptionResult]:
    """Rebuild the filtered cache from the last downloaded YAML without network I/O."""
    subscription = config.get("subscription", {})
    source_url = str(subscription.get("source_url", "")).strip()
    cache_path = Path(str(subscription.get("cache_path") or "/data/subscription-filtered.yaml"))
    source_path = Path(str(subscription.get("last_source_path") or "/data/subscription-source.yaml"))
    try:
        source_text = source_path.read_text(encoding="utf-8")
        data = load_yaml_text(source_text)
        prepared_config = config_with_dynamic_regions(data, config)
        before_region_ids = {str(region.get("id")) for region in config.get("regions", [])}
        after_region_ids = {str(region.get("id")) for region in prepared_config.get("regions", [])}
        filtered, filter_result = filter_config_data(data, prepared_config)
        rendered = dump_yaml(filtered)
        old_rendered = cache_path.read_text(encoding="utf-8") if cache_path.exists() else None
        atomic_write(cache_path, rendered)

        updated_config = dict(config)
        updated_config["regions"] = prepared_config["regions"]
        filter_payload = filter_result.to_dict()
        filter_payload["config_path"] = str(source_path)
        return updated_config, SubscriptionResult(
            ok=True,
            changed=old_rendered != rendered,
            source_url=source_url,
            cache_path=str(cache_path),
            generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            regions_added=sorted(after_region_ids - before_region_ids),
            filter_result=filter_payload,
            subscription_info=None,
        )
    except (OSError, ValueError) as exc:
        return config, SubscriptionResult(
            ok=False,
            changed=False,
            source_url=source_url,
            cache_path=str(cache_path),
            generated_at=None,
            regions_added=[],
            filter_result=None,
            subscription_info=None,
            error=str(exc),
        )


def install_filtered_config(config: dict[str, Any]) -> InstallResult:
    subscription = config.get("subscription", {})
    cache_path = Path(str(subscription.get("cache_path") or "/data/subscription-filtered.yaml"))
    output_path = Path(
        str(
            subscription.get("output_config_path")
            or "/etc/openclash/config/openclash-region-filter.yaml"
        )
    )

    try:
        rendered = cache_path.read_text(encoding="utf-8")
        # Parse before installing so a corrupt cache cannot replace a usable config file.
        load_yaml_text(rendered)
        previous = output_path.read_text(encoding="utf-8") if output_path.exists() else None
        atomic_write(output_path, rendered)
        return InstallResult(
            ok=True,
            changed=previous != rendered,
            source_path=str(cache_path),
            output_config_path=str(output_path),
            installed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
    except (OSError, ValueError) as exc:
        return InstallResult(
            ok=False,
            changed=False,
            source_path=str(cache_path),
            output_config_path=str(output_path),
            installed_at=None,
            error=str(exc),
        )
