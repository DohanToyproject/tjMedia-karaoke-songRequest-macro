import json
import random
import time
import platform
import subprocess
import socket
import shutil
from typing import Tuple

from modules.tjKoraoke import TjClient, GENRE_TO_DT_CODE
import modules.freeProxy as fp

def logging(msg):
    print("[Log]", msg)

def jitter(min_ms, max_ms):
    if min_ms and max_ms and max_ms >= min_ms:
        time.sleep(random.uniform(min_ms, max_ms) / 1000.0)

def resolve_dt_code(song: dict) -> str | None:
    if "dt_code" in song and song["dt_code"]:
        return str(song["dt_code"])
    genre = song.get("genre")
    if genre:
        return GENRE_TO_DT_CODE.get(str(genre).strip(), None)
    return None

def proxy_url_from_tuple(t, proxy_type: str) -> str:
    # freeProxy.get_list() → [(ip:port, uptime_pct), ...]
    hostport = t[0] if isinstance(t, (list, tuple)) else str(t)
    if proxy_type.upper() == "HTTP":
        return f"http://{hostport}"
    else:
        return f"socks5://{hostport}"

def process_song(client: TjClient, song: dict, allow_propose: bool, retries: int, retry_delay_ms: int):
    title = song.get("title") or song.get("songTitle")
    singer = song.get("singer")
    idx = song.get("idx")

    if not title or not singer:
        logging(f"곡 정보 부족: {song}")
        return

    # 1) idx 미지정 시 검색
    if not idx:
        for attempt in range(retries + 1):
            try:
                idx = client.search_idx(singer=singer, title=title, exact=True)
                break
            except Exception as e:
                logging(f"검색 실패({title}/{singer}) attempt={attempt+1}: {e}")
                if attempt < retries:
                    time.sleep(retry_delay_ms / 1000.0)

    # 2) 추천
    if idx:
        try:
            res = client.recommend(idx)
            ok = (res.get("result") == "success" and res.get("code") in (None, "000")) or (res.get("status") in (200, 204))
            logging(f"추천 {'성공' if ok else '응답확인필요'}: {title} / {singer} (idx={idx}) -> {res}")
            return
        except Exception as e:
            logging(f"추천 실패(idx={idx}): {e}")
            return

    # 3) 없으면 신청(save_propose)
    if allow_propose:
        dt_code = resolve_dt_code(song)
        if not dt_code:
            logging(f"검색 결과 없음 + dt_code/genre 미지정: 신청 불가 → {title}/{singer}")
            return
        try:
            res = client.save_propose(dt_code=dt_code, singer=singer, title=title,
                                      po_name=song.get("po_name", "익명"),
                                      po_content=song.get("po_content", "반주곡 신청"))
            ok = (res.get("result") == "success" and res.get("code") in (None, "000")) or (res.get("status") in (200, 201))
            logging(f"신청 {'성공' if ok else '응답확인필요'}: {title}/{singer} (dt_code={dt_code}) -> {res}")
        except Exception as e:
            logging(f"신청 실패: {title}/{singer} (dt_code={dt_code}) → {e}")
    else:
        logging(f"검색 결과 없음: {title}/{singer} (신청 비활성화)")
    return

def run_with_proxy(songs, proxy_type: str, limit: int, settings_run: dict):
    # 프록시 수집
    proxies = []
    try:
        freeProxy = fp.proxies(str(proxy_type))
        proxies = freeProxy.get_list()
        logging(f"프록시 수집 완료: {len(proxies)}개")
    except Exception as e:
        logging(f"프록시 수집 실패: {e}")
        return

    use_list = proxies[:limit] if (limit and limit > 0) else proxies
    if not use_list:
        logging("사용할 프록시가 없습니다.")
        return

    for idx_p, p in enumerate(use_list, start=1):
        purl = proxy_url_from_tuple(p, proxy_type)
        for i, song in enumerate(songs, start=1):
            client = TjClient(timeout=settings_run.get("timeoutSec", 7))
            client.set_proxy(purl)
            logging(f"[Proxy {idx_p}/{len(use_list)} | Song {i}/{len(songs)}] {song.get('title')} / {song.get('singer')} | proxy={purl}")
            try:
                process_song(
                    client=client,
                    song=song,
                    allow_propose=settings_run.get("allowProposeIfNotFound", True),
                    retries=settings_run.get("retries", 1),
                    retry_delay_ms=settings_run.get("retryDelayMs", 400),
                )
            finally:
                client.close()
            dm = settings_run.get("delayMs", {"min": 400, "max": 1200})
            jitter(dm.get("min", 400), dm.get("max", 1200))

# -------------------------
# Tor service management
# -------------------------
def _detect_platform() -> str:
    """'linux', 'darwin', or 'other'"""
    sys = platform.system()
    if sys == "Linux":
        return "linux"
    if sys == "Darwin":
        return "darwin"
    return "other"

def _command_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def _run_cmd(cmd: list) -> Tuple[int, str, str]:
    """subprocess.run wrapper -> (returncode, stdout, stderr)"""
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as e:
        return 1, "", str(e)

def start_tor_service(service="tor"):
    plat = _detect_platform()
    if plat == "darwin":
        if not _command_exists("brew"):
            logging("brew가 시스템에 없습니다. mac에서 'brew services'로 토르를 시작할 수 없습니다.")
            return False
        cmd = ["brew", "services", "start", service]
    elif plat == "linux":
        if not _command_exists("systemctl"):
            logging("systemctl을 찾을 수 없습니다. Linux에서 systemctl로 토르를 시작할 수 없습니다.")
            return False
        cmd = ["systemctl", "start", service]
    else:
        logging(f"지원되지 않는 플랫폼({plat})입니다. 토르 서비스 자동 제어를 건너뜁니다.")
        return False

    code, out, err = _run_cmd(cmd)
    logging(f"Start '{service}' => rc={code}; out={out}; err={err}")
    return code == 0

def restart_tor_service(service="tor"):
    plat = _detect_platform()
    if plat == "darwin":
        if not _command_exists("brew"):
            logging("brew가 시스템에 없습니다. mac에서 'brew services'로 토르를 재시작할 수 없습니다.")
            return False
        cmd = ["brew", "services", "restart", service]
    elif plat == "linux":
        if not _command_exists("systemctl"):
            logging("systemctl을 찾을 수 없습니다. Linux에서 systemctl로 토르를 재시작할 수 없습니다.")
            return False
        cmd = ["systemctl", "restart", service]
    else:
        logging(f"지원되지 않는 플랫폼({_detect_platform()})입니다. 토르 서비스 자동 제어를 건너뜁니다.")
        return False

    code, out, err = _run_cmd(cmd)
    logging(f"Restart '{service}' => rc={code}; out={out}; err={err}")
    return code == 0

def port_is_listening(host: str, port: int, timeout_s: float = 1.0) -> bool:
    """TCP 연결 시도로 포트 열림 여부 확인"""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except Exception:
        return False

def wait_for_socks(host: str, port: int, max_wait: int = 60) -> bool:
    """max_wait 초 동안 주기적으로 포트가 열리는지 확인"""
    logging(f"대기: tor SOCKS 포트 {port}가 열릴 때까지 최대 {max_wait}s...")
    waited = 0
    interval = 1
    while waited < max_wait:
        if port_is_listening(host, port, timeout_s=1.0):
            logging("SOCKS 포트 열림.")
            return True
        time.sleep(interval)
        waited += interval
    logging(f"경고: SOCKS 포트({port})가 {max_wait}s 내에 열리지 않았습니다.")
    return False

def run_with_tor(songs, rounds: int, settings_run: dict):
    # Tor SOCKS5는 고정 주소 사용
    tor_proxy_url = "socks5://127.0.0.1:9050"
    SOCKS_HOST = "127.0.0.1"
    SOCKS_PORT = settings_run.get("tor", {}).get("socksPort", 9050)

    # 서비스 제어 옵션
    tor_opt = settings_run.get("tor", {"manageService": True, "serviceName": "tor"})
    manage_service = tor_opt.get("manageService", True)
    service_name = tor_opt.get("serviceName", "tor")
    max_wait_port = tor_opt.get("waitPortSec", 60)

    # 플랫폼 체크: mac과 linux 지원 (없으면 manage_service 자동 false)
    plat = _detect_platform()
    if manage_service and plat not in ("linux", "darwin"):
        logging(f"현재 플랫폼({plat})에서는 자동 서비스 제어를 지원하지 않습니다. manageService를 False로 설정하고 수동으로 tor를 실행하세요.")
        manage_service = False

    if manage_service:
        ok = start_tor_service(service=service_name)
        if not ok:
            logging("tor 서비스를 자동으로 시작하지 못했습니다. 계속하려면 수동으로 tor를 실행하세요.")
        # 포트가 열릴 때까지 대기
        wait_for_socks(SOCKS_HOST, SOCKS_PORT, max_wait=max_wait_port)

    rounds = rounds if rounds and rounds > 0 else 1
    for r in range(1, rounds + 1):
        if manage_service and r > 1:
            ok = restart_tor_service(service=service_name)
            if not ok:
                logging("tor 서비스를 재시작하지 못했습니다. 수동 재시작을 시도하세요.")
            wait_for_socks(SOCKS_HOST, SOCKS_PORT, max_wait=max_wait_port)

        for i, song in enumerate(songs, start=1):
            client = TjClient(timeout=settings_run.get("timeoutSec", 7))
            client.set_proxy(tor_proxy_url)
            logging(f"[Tor round {r}/{rounds} | Song {i}/{len(songs)}] {song.get('title')} / {song.get('singer')}")
            try:
                process_song(
                    client=client,
                    song=song,
                    allow_propose=settings_run.get("allowProposeIfNotFound", True),
                    retries=settings_run.get("retries", 1),
                    retry_delay_ms=settings_run.get("retryDelayMs", 400),
                )
            finally:
                client.close()
            dm = settings_run.get("delayMs", {"min": 400, "max": 1200})
            jitter(dm.get("min", 400), dm.get("max", 1200))

if __name__ == "__main__":
    with open("setting.macro.json", "r", encoding="utf-8") as f:
        settings = json.load(f)

    run = settings.get("run", {})
    mode = run.get("mode", "none")  # none | proxy | tor | all
    proxy_type = run.get("proxy", {}).get("type", "SOCKS")  # "SOCKS" or "HTTP"
    proxy_limit = run.get("proxy", {}).get("limit", 20)     # 사용할 프록시 개수 제한
    rounds = run.get("rounds", 1)                            # tor 라운드 수 (IP 회전 횟수)
    songs = settings.get("songs", [])

    if not songs:
        logging("songs 설정이 비어있습니다.")
        exit(0)

    logging(f"실행 모드: {mode}")

    if mode == "proxy":
        run_with_proxy(songs, proxy_type=proxy_type, limit=proxy_limit, settings_run=run)
    elif mode == "tor":
        run_with_tor(songs, rounds=rounds, settings_run=run)
    elif mode == "all":
        # 간단히 proxy 먼저 한 바퀴, 그다음 tor n라운드
        run_with_proxy(songs, proxy_type=proxy_type, limit=proxy_limit, settings_run=run)
        run_with_tor(songs, rounds=rounds, settings_run=run)
    else:
        # 로컬 세션(프록시/토르 없이 1회)
        logging("프록시/토르 없이 단발 실행")
        client = TjClient(timeout=run.get("timeoutSec", 7))
        try:
            for i, song in enumerate(songs, start=1):
                logging(f"[Local | Song {i}/{len(songs)}] {song.get('title')} / {song.get('singer')}")
                process_song(
                    client=client,
                    song=song,
                    allow_propose=run.get("allowProposeIfNotFound", True),
                    retries=run.get("retries", 1),
                    retry_delay_ms=run.get("retryDelayMs", 400),
                )
                dm = run.get("delayMs", {"min": 400, "max": 1200})
                jitter(dm.get("min", 400), dm.get("max", 1200))
        finally:
            client.close()