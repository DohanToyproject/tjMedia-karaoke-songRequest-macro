import secrets
import requests

BASE_URL = "https://www.tjmedia.com"

GENRE_TO_DT_CODE = {
    "일반가요": "10",
    "국내가요": "10",
    "해외팝송": "20",
    "pop": "20",
    "팝": "20",
    "jpop": "30",
    "JPOP": "30",
    "JPop": "30",
    "일본곡": "30",
    "중국곡": "40",
    "cpop": "40",
    "애니메이션": "50",
    "애니": "50",
    "anison": "50",
    "뮤지컬": "60",
    "musical": "60"
}

def _gen_csrf_token(length=32):
    return secrets.token_urlsafe(length)

class TjClient:
    def __init__(self, timeout=7, user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"):
        self.timeout = timeout
        self.session = requests.Session()
        self.csrf = _gen_csrf_token()
        # CSRF 설정
        self.session.cookies.set("CSRF_TOKEN", self.csrf, domain="www.tjmedia.com")
        self.session.headers.update({
            "User-Agent": user_agent,
            "X-CSRF-TOKEN": self.csrf,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Origin": "https://www.tjmedia.com",
            "Referer": "https://www.tjmedia.com/",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
        })

    def set_proxy(self, proxy_url: str | None):
        if proxy_url:
            self.session.proxies = {"http": proxy_url, "https": proxy_url}
        else:
            self.session.proxies = {}

    def search_idx(self, singer: str, title: str, page_no: int = 1, exact: bool = True) -> int | None:
        """
        검색해서 첫 결과의 idx 반환. exact=True면 가수/제목 완전 일치 우선 선택.
        """
        url = f"{BASE_URL}/song/searchPropose"
        data = {
            "po_song_singer": singer,
            "po_song_title": title,
            "pageNo": str(page_no),
        }
        r = self.session.post(url, data=data, timeout=self.timeout)
        r.raise_for_status()
        j = r.json()
        if j.get("result") != "success":
            return None

        view = j.get("data", {}).get("viewData", {})
        lst = view.get("list", []) or []
        if not lst:
            return None

        if exact:
            def norm(s: str) -> str:
                return (s or "").strip().lower()
            ns, nt = norm(singer), norm(title)
            for row in lst:
                if norm(row.get("po_song_singer")) == ns and norm(row.get("po_song_title")) == nt:
                    return row.get("idx")

        return lst[0].get("idx")

    def recommend(self, idx: int | str) -> dict:
        url = f"{BASE_URL}/song/recommend"
        data = {"idx": str(idx)}
        r = self.session.post(url, data=data, timeout=self.timeout)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {"status": r.status_code, "text": r.text}

    def save_propose(self, dt_code: int | str, singer: str, title: str, po_name="익명", po_content="반주곡 신청") -> dict:
        url = f"{BASE_URL}/song/save_propose"
        data = {
            "dt_code": str(dt_code),
            "po_song_singer": singer,
            "po_song_title": title,
            "po_name": po_name,
            "po_content": po_content,
        }
        r = self.session.post(url, data=data, timeout=self.timeout)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {"status": r.status_code, "text": r.text}

    def close(self):
        self.session.close()