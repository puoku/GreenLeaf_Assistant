from __future__ import annotations

import re
from urllib.parse import urlparse

SUSPICIOUS_HOSTS = {
    'bit.ly', 'tinyurl.com', 'goo.su', 'is.gd', 'cutt.ly', 't.me', 'telegram.me', 'telegra.ph'
}
SUSPICIOUS_KEYWORDS = {'bonus', 'gift', 'airdrop', 'crypto', 'bet', 'casino', 'joinchat', 'free-money'}
URL_RE = re.compile(r'((?:https?://|www\.)\S+|t\.me/\S+)', re.IGNORECASE)
TG_MENTION_RE = re.compile(r'(?<!\w)@[A-Za-z][A-Za-z0-9_]{4,}', re.IGNORECASE)
IP_RE = re.compile(r'https?://\d{1,3}(?:\.\d{1,3}){3}')


def extract_urls(text: str) -> list[str]:
    urls = [m.group(0) for m in URL_RE.finditer(text or '')]
    for mention in TG_MENTION_RE.findall(text or ''):
        urls.append(mention)
    return urls


def is_suspicious_link(text: str, bot_username: str) -> bool:
    lowered = (text or '').lower()
    if IP_RE.search(lowered):
        return True
    for url in extract_urls(text):
        u = url.lower()
        if u.startswith('@') and bot_username.lower().strip('@') not in u:
            return True
        if u.startswith('t.me/') and bot_username.lower().strip('@') not in u:
            return True
        parsed = urlparse(u if u.startswith('http') else f'https://{u}')
        host = parsed.netloc.replace('www.', '')
        if host in SUSPICIOUS_HOSTS:
            return True
        if 'xn--' in host:
            return True
        if any(word in u for word in SUSPICIOUS_KEYWORDS):
            return True
    return False
