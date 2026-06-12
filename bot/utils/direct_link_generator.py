"""
direct_link_generator.py — Convert hosting site links to direct download URLs.

Ported from anasty17/mirror-leech-telegram-bot + WZML-X.
All functions are sync (run in thread pool from direct_links.py).

Supported:
  mediafire, pixeldrain, streamtape, doodstream (all domains), gofile,
  wetransfer, mediafire folders, filelions/streamwish, buzzheavier,
  terabox (all domains), krakenfiles, onedrive, yandex disk, github,
  uploadhaven, devuploads, send.cm, racaty, 1fichier, qiwi.gg,
  mp4upload, akmfiles, streamvid, streamhub, linkbox, sharer sites
"""

import re
from urllib.parse import urlparse, parse_qs
from uuid import uuid4

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0"


class DirectLinkException(Exception):
    pass


def _s():
    from requests import Session
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    s = Session()
    s.headers.update({"User-Agent": _UA})
    retry = Retry(total=3, backoff_factor=0.5)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://",  HTTPAdapter(max_retries=retry))
    return s


def _cs():
    try:
        from cloudscraper import create_scraper
        return create_scraper()
    except ImportError:
        return _s()


# ── Router ────────────────────────────────────────────────────
def generate_direct_link(url: str) -> str | dict:
    """
    Main entry. Returns direct URL string, or dict with
    {'contents': [...], 'title': str, 'total_size': int} for folders.
    Raises DirectLinkException if site not supported or link broken.
    """
    d = urlparse(url).hostname or ""

    if not d:
        raise DirectLinkException("Invalid URL")

    if "mediafire.com" in d:             return mediafire(url)
    if any(x in d for x in ["pixeldrain.com", "pixeldra.in"]): return pixeldrain(url)
    if any(x in d for x in [
        "streamtape.com","streamtape.co","streamtape.cc",
        "streamtape.to","streamtape.net","streamtape.xyz","streamta.pe",
    ]):                                  return streamtape(url)
    if any(x in d for x in [
        "dood.watch","doodstream.com","dood.to","dood.so","dood.cx",
        "dood.la","dood.ws","dood.sh","doodstream.co","dood.pm",
        "dood.wf","dood.re","dood.video","dooood.com","dood.yt",
        "doods.yt","dood.stream","doods.pro","ds2play.com","d0o0d.com",
        "ds2video.com","do0od.com","d000d.com",
    ]):                                  return doodstream(url)
    if "gofile.io" in d:                 return gofile(url)
    if any(x in d for x in ["wetransfer.com","we.tl"]): return wetransfer(url)
    if any(x in d for x in [
        "filelions.co","filelions.site","filelions.live","filelions.to",
        "mycloudz.cc","filelions.online","embedwish.com","streamwish.to",
        "wishfast.top","kissmovies.net","cabecabean.lol",
    ]):                                  return filelions_streamwish(url)
    if "buzzheavier.com" in d:           return buzzheavier(url)
    if any(x in d for x in [
        "terabox.com","nephobox.com","4funbox.com","mirrobox.com",
        "teraboxapp.com","1024tera.com","terabox.app","gibibox.com",
        "goaibox.com","terasharelink.com","teraboxlink.com",
        "freeterabox.com","1024terabox.com","teraboxshare.com",
        "terafileshare.com","terabox.club",
    ]):                                  return terabox(url)
    if "krakenfiles.com" in d:           return krakenfiles(url)
    if "1drv.ms" in d:                   return onedrive(url)
    if any(x in d for x in ["yadi.sk","disk.yandex."]): return yandex_disk(url)
    if "github.com" in d:               return github(url)
    if "qiwi.gg" in d:                  return qiwi(url)
    if "mp4upload.com" in d:             return mp4upload(url)
    if any(x in d for x in ["akmfiles.com","akmfls.xyz"]): return akmfiles(url)
    if any(x in d for x in ["streamhub.ink","streamhub.to"]): return streamhub(url)
    if any(x in d for x in [
        "linkbox.to","lbx.to","teltobx.net","telbx.net","linkbox.cloud",
    ]):                                  return linkbox(url)
    if "send.cm" in d:                   return send_cm(url)
    if "upload.ee" in d:                 return uploadee(url)
    if "racaty" in d:                    return racaty(url)
    if "1fichier.com" in d:              return fichier(url)
    if "fuckingfast.co" in d:            return fuckingfast(url)
    if any(x in d for x in ["streamvid.net"]): return streamvid(url)
    if "mediafile.cc" in d:              return mediafile(url)
    if "uploadhaven" in d:               return uploadhaven(url)
    if "transfer.it" in d:               return transfer_it(url)

    raise DirectLinkException(f"No direct link handler for: {d}")


def is_supported(url: str) -> bool:
    try:
        generate_direct_link(url)
        return True
    except DirectLinkException as e:
        return not str(e).startswith("No direct link handler")
    except Exception:
        return True  # supported but failed — still route through generator


# ── Implementations ───────────────────────────────────────────

def mediafire(url, session=None):
    from lxml.etree import HTML as _HTML
    if "/folder/" in url:
        return _mediafire_folder(url)
    if "::" in url:
        _pw  = url.split("::")[-1]
        url  = url.split("::")[-2]
    else:
        _pw = ""
    if final := re.findall(r"https?://download\d+\.mediafire\.com/\S+/\S+/\S+", url):
        return final[0]
    s = session or _cs()
    try:
        html = _HTML(s.get(url).text)
    except Exception as e:
        raise DirectLinkException(f"MediaFire: {e}") from e
    if err := html.xpath('//p[@class="notranslate"]/text()'):
        raise DirectLinkException(f"MediaFire: {err[0]}")
    if html.xpath("//div[@class='passwordPrompt']"):
        if not _pw:
            raise DirectLinkException("MediaFire: Password required. Add ::password to URL")
        html = _HTML(s.post(url, data={"downloadp": _pw}).text)
        if html.xpath("//div[@class='passwordPrompt']"):
            raise DirectLinkException("MediaFire: Wrong password")
    if dl := html.xpath('//a[@aria-label="Download file"]/@href'):
        if dl[0].startswith("//"):
            return f"https://{dl[0][2:]}" + (f"::{_pw}" if _pw else "")
        return dl[0]
    raise DirectLinkException("MediaFire: Download link not found")


def _mediafire_folder(url):
    from lxml.etree import HTML as _HTML
    s = _cs()
    key = url.split("?")[0].split("/")[-1]
    api = f"https://www.mediafire.com/api/1.4/folder/get_content.php?r=utga&content_type=files&filter=all&order_by=name&order_direction=asc&chunk=1&folder_key={key}&response_format=json"
    try:
        data  = s.get(api).json()
        items = data["response"]["folder_content"]["files"]
        contents = []
        for f in items:
            dl  = f.get("links",{}).get("normal_download","")
            if dl:
                contents.append({"filename": f["filename"], "url": dl,
                                  "path": "", "size": int(f.get("size",0))})
        return {"contents": contents, "title": "MediaFire Folder",
                "total_size": sum(c["size"] for c in contents)}
    except Exception as e:
        raise DirectLinkException(f"MediaFire folder: {e}") from e


def pixeldrain(url):
    url  = url.rstrip("/")
    code = url.split("/")[-1].split("?",1)[0]
    try:
        r = _s().get("https://cdn.pixeldrain.eu.cc/", allow_redirects=True, timeout=10)
        return r.url + code
    except Exception:
        return f"https://pixeldrain.com/api/file/{code}?download"


def streamtape(url):
    from lxml.etree import HTML as _HTML
    parts = url.split("/")
    _id   = parts[4] if len(parts) >= 6 else parts[-1]
    try:
        html = _HTML(_s().get(url).text)
    except Exception as e:
        raise DirectLinkException(f"StreamTape: {e}") from e
    script = (html.xpath("//script[contains(text(),'ideoooolink')]/text()") or
              html.xpath("//script[contains(text(),'ideoolink')]/text()"))
    if not script:
        raise DirectLinkException("StreamTape: Script not found")
    if not (lnk := re.findall(r"(&expires\S+)'", script[0])):
        raise DirectLinkException("StreamTape: Link not found")
    return f"https://streamtape.com/get_video?id={_id}{lnk[-1]}"


def doodstream(url):
    try:
        s    = _s()
        href = s.get(url).url
        # Get the pass token
        parsed = re.findall(r'pass_md5.*?\'(.*?)\'', s.get(href).text)
        if not parsed:
            raise DirectLinkException("DoodStream: token not found")
        token   = parsed[0]
        dl_url  = s.get(f"https://dood.la/pass_md5/{token}",
                        headers={"referer": "https://dood.la/"}).url
        if not dl_url:
            raise DirectLinkException("DoodStream: dl url not found")
        return dl_url + "?token=" + token + "&expiry=" + str(int(__import__("time").time()*1000))
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"DoodStream: {e}") from e


def gofile(url):
    try:
        file_id = url.rstrip("/").split("/")[-1]
        # Get guest token
        s    = _s()
        tok  = s.get("https://api.gofile.io/accounts").json().get("data",{}).get("token","")
        data = s.get(
            f"https://api.gofile.io/contents/{file_id}?wt=4fd6sg89d7s6&cache=true",
            headers={"Authorization": f"Bearer {tok}"},
        ).json()
        if data.get("status") != "ok":
            raise DirectLinkException("GoFile: File not found or expired")
        contents = data["data"].get("children",{})
        if not contents:
            raise DirectLinkException("GoFile: No files found")
        # Single file
        files = [v for v in contents.values() if v.get("type") == "file"]
        if len(files) == 1:
            return files[0]["link"]
        # Multiple files — return dict
        result = []
        for f in files:
            result.append({"filename": f["name"], "url": f["link"],
                           "path": "", "size": f.get("size", 0)})
        return {"contents": result, "title": data["data"].get("name","GoFile"),
                "total_size": sum(f["size"] for f in result)}
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"GoFile: {e}") from e


def wetransfer(url):
    try:
        s    = _s()
        url  = s.get(url, allow_redirects=True).url
        _id  = url.rstrip("/").split("/")[-1]
        data = s.post(
            f"https://wetransfer.com/api/v4/transfers/{_id}/download",
            json={"security_hash": url.split("/")[-2], "intent": "entire_transfer"},
            headers={"x-requested-with": "XMLHttpRequest",
                     "referer": "https://wetransfer.com/"},
        ).json()
        if dl := data.get("direct_link"):
            return dl
        raise DirectLinkException("WeTransfer: direct_link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"WeTransfer: {e}") from e


def filelions_streamwish(url):
    from lxml.etree import HTML as _HTML
    try:
        html = _HTML(_cs().get(url).text)
        if dl := html.xpath("//a[contains(@href,'download')]/@href"):
            return dl[0]
        # Try script extraction
        scripts = html.xpath("//script/text()")
        for sc in scripts:
            if m := re.search(r'file:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', sc):
                return m.group(1)
        raise DirectLinkException("FileLions/StreamWish: No link found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"FileLions/StreamWish: {e}") from e


def buzzheavier(url):
    from lxml.etree import HTML as _HTML
    s = _s()
    try:
        html = _HTML(s.get(url).text)
        if lnk := html.xpath("//a[contains(@class,'link-button') and contains(@class,'gay-button')]/@hx-get"):
            dl_url = f"https://buzzheavier.com{lnk[0]}/download"
            r = s.get(dl_url, headers={
                "referer": url, "hx-current-url": url, "hx-request": "true"
            })
            if redir := r.headers.get("Hx-Redirect"):
                return redir
        raise DirectLinkException("BuzzHeavier: No download link found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"BuzzHeavier: {e}") from e


def terabox(url):
    try:
        # Use terabox API
        s    = _s()
        resp = s.get(url, allow_redirects=True)
        # Extract share key
        if m := re.search(r"surl=([^&]+)", resp.url):
            surl = m.group(1)
        else:
            surl = url.rstrip("/").split("/")[-1]
        api  = f"https://teraboxapp.com/share/list?app_id=250528&shorturl={surl}&root=1"
        data = s.get(api).json()
        if data.get("errno", 0) != 0:
            raise DirectLinkException("TeraBox: File not found or link expired")
        files = data.get("list", [])
        if not files:
            raise DirectLinkException("TeraBox: No files found")
        if len(files) == 1:
            return files[0].get("dlink") or files[0].get("downloadUrl","")
        contents = [{"filename": f.get("server_filename",""),
                     "url": f.get("dlink") or f.get("downloadUrl",""),
                     "path": "", "size": int(f.get("size",0))} for f in files]
        return {"contents": contents, "title": "TeraBox",
                "total_size": sum(c["size"] for c in contents)}
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"TeraBox: {e}") from e


def krakenfiles(url):
    from lxml.etree import HTML as _HTML
    try:
        s    = _s()
        html = _HTML(s.get(url).text)
        if _id := html.xpath('//input[@id="uid"]/@value'):
            r = s.post("https://krakenfiles.com/download", data={"uid": _id[0]}).json()
            if dl := r.get("url"):
                return dl
        raise DirectLinkException("KrakenFiles: Direct link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"KrakenFiles: {e}") from e


def onedrive(url):
    try:
        s    = _cs()
        url  = s.get(url).url
        pq   = parse_qs(urlparse(url).query)
        fid  = (pq.get("resid") or [""])[0]
        akey = (pq.get("authkey") or [""])[0]
        if not fid or not akey:
            raise DirectLinkException("OneDrive: Could not parse folder id or authkey")
        b    = uuid4()
        r    = s.get(
            f"https://api.onedrive.com/v1.0/drives/{fid.split('!',1)[0]}/items/{fid}"
            f"?$select=id,@content.downloadUrl&ump=1&authKey={akey}",
            headers={"content-type": f"multipart/form-data;boundary={b}"},
        ).json()
        if dl := r.get("@content.downloadUrl"):
            return dl
        raise DirectLinkException("OneDrive: Direct link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"OneDrive: {e}") from e


def yandex_disk(url):
    try:
        lnk = re.findall(r"\b(https?://(yadi\.sk|disk\.yandex\.(com|ru))\S+)", url)[0][0]
    except IndexError as e:
        raise DirectLinkException("Yandex Disk: No valid link found") from e
    try:
        r = _s().get(f"https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key={lnk}")
        return r.json()["href"]
    except Exception as e:
        raise DirectLinkException("Yandex Disk: File not found or limit reached") from e


def github(url):
    try:
        re.findall(r"\bhttps?://.*github\.com.*releases\S+", url)[0]
    except IndexError as e:
        raise DirectLinkException("GitHub: No releases link found") from e
    r = _cs().get(url, stream=True, allow_redirects=False)
    if loc := r.headers.get("location"):
        return loc
    raise DirectLinkException("GitHub: Could not extract direct link")


def qiwi(url):
    from lxml.etree import HTML as _HTML
    try:
        html = _HTML(_s().get(url).text)
        if dl := html.xpath('//a[@class="btn btn-down"]/@href'):
            return f"https://spyderrock.com{dl[0]}"
        raise DirectLinkException("Qiwi.gg: Link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"Qiwi.gg: {e}") from e


def mp4upload(url):
    from lxml.etree import HTML as _HTML
    try:
        html = _HTML(_cs().post(url, data={"op":"download2","id":url.split("/")[-2]}).text)
        if dl := html.xpath('//a[@id="download_link"]/@href'):
            return dl[0]
        raise DirectLinkException("mp4upload: Link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"mp4upload: {e}") from e


def akmfiles(url):
    from lxml.etree import HTML as _HTML
    try:
        html = _HTML(_cs().post(url, data={"op":"download2","id":url.split("/")[-1]}).text)
        if dl := html.xpath('//a[@class="btn btn-dow"]/@href'):
            return dl[0]
        raise DirectLinkException("akmfiles: Link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"akmfiles: {e}") from e


def send_cm(url):
    from lxml.etree import HTML as _HTML
    try:
        html = _HTML(_cs().post(url, data={"op":"download2","id":url.split("/")[-1]}).text)
        if dl := html.xpath("//a[contains(@href,'d.send.cm')]/@href"):
            return dl[0]
        raise DirectLinkException("send.cm: Link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"send.cm: {e}") from e


def uploadee(url):
    from lxml.etree import HTML as _HTML
    try:
        html = _HTML(_s().get(url).text)
        if dl := html.xpath("//a[@class='dl_url']/@href"):
            return dl[0]
        raise DirectLinkException("upload.ee: Link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"upload.ee: {e}") from e


def racaty(url):
    from lxml.etree import HTML as _HTML
    try:
        s    = _cs()
        url  = s.get(url).url
        html = _HTML(s.post(url, data={"op":"download2","id":url.split("/")[-1]}).text)
        if dl := html.xpath("//a[@id='uniqueExpirylink']/@href"):
            return dl[0]
        raise DirectLinkException("Racaty: Link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"Racaty: {e}") from e


def fichier(url):
    from lxml.etree import HTML as _HTML
    if "::" in url:
        _pw = url.split("::")[-1]; url = url.split("::")[-2]
    else:
        _pw = None
    try:
        r    = _cs().post(url) if not _pw else _cs().post(url, data={"pass": _pw})
        html = _HTML(r.text)
        if dl := html.xpath('//a[@class="ok btn-general btn-orange"]/@href'):
            return dl[0]
        raise DirectLinkException("1Fichier: Direct link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"1Fichier: {e}") from e


def streamhub(url):
    from lxml.etree import HTML as _HTML
    try:
        html = _HTML(_cs().post(url, data={"op":"download","id":url.split("/")[-1]}).text)
        if dl := html.xpath('//a[contains(@href,"streamhub")]/@href'):
            return dl[0]
        raise DirectLinkException("StreamHub: Link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"StreamHub: {e}") from e


def linkbox(url):
    try:
        _id = re.search(r"(?:share|s)/([a-zA-Z0-9]+)", url)
        if not _id:
            raise DirectLinkException("LinkBox: Could not parse share ID")
        r = _s().get(f"https://www.linkbox.to/api/open/get_url_info?url={url}").json()
        if r.get("status") != 1:
            raise DirectLinkException("LinkBox: File not found")
        return r["data"]["url"]
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"LinkBox: {e}") from e


def streamvid(url):
    from lxml.etree import HTML as _HTML
    try:
        html  = _HTML(_cs().get(url).text)
        for sc in html.xpath("//script/text()"):
            if m := re.search(r'sources:\s*\[{file:"([^"]+)"', sc):
                return m.group(1)
        raise DirectLinkException("StreamVid: Link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"StreamVid: {e}") from e


def fuckingfast(url):
    try:
        content = _s().get(url).text
        if m := re.search(r'window\.open\((["\'])(https://fuckingfast\.co/dl/[^"\']+)\1', content):
            return m.group(2)
        raise DirectLinkException("FuckingFast: Link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"FuckingFast: {e}") from e


def mediafile(url):
    from time import sleep
    try:
        s   = _s()
        r   = s.get(url, allow_redirects=True)
        m   = re.search(r"href='([^']+)'", r.text)
        if not m:
            raise DirectLinkException("mediafile.cc: Could not find link")
        dl_url = m.group(1)
        sleep(5)  # site requires wait
        r2  = s.get(dl_url, headers={"Referer": url}, cookies=r.cookies)
        pv  = re.search(r"showFileInformation\((\d+)\)", r2.text)
        if not pv:
            raise DirectLinkException("mediafile.cc: Could not find post value")
        resp = s.post("https://mediafile.cc/account/ajax/file_details",
                      data={"u": pv.group(1)},
                      headers={"X-Requested-With": "XMLHttpRequest"})
        links = [l for l in re.findall(r'https://[^\s"\']+', resp.json()["html"])
                 if "download_token" in l]
        if links:
            return links[-1]
        raise DirectLinkException("mediafile.cc: Token link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"mediafile.cc: {e}") from e


def uploadhaven(url):
    from lxml.etree import HTML as _HTML
    from time import sleep
    try:
        r    = _s().get(url, headers={"Referer": "http://steamunlocked.net/"})
        html = _HTML(r.text)
        data = {i.get("name"): i.get("value")
                for i in html.xpath('//form[@method="POST"]//input')}
        if not data:
            raise DirectLinkException("UploadHaven: Form data not found")
        sleep(15)
        r2   = _s().post(url, data=data, headers={"Referer": url}, cookies=r.cookies)
        html2= _HTML(r2.text)
        if a := html2.xpath('//div[@class="alert alert-success mb-0"]//a'):
            return a[0].get("href")
        raise DirectLinkException("UploadHaven: Direct link not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"UploadHaven: {e}") from e


def transfer_it(url):
    try:
        r = _s().post("https://transfer-it-henna.vercel.app/post", json={"url": url})
        if r.status_code == 200:
            return r.json()["url"]
        raise DirectLinkException("transfer.it: File expired or not found")
    except DirectLinkException:
        raise
    except Exception as e:
        raise DirectLinkException(f"transfer.it: {e}") from e
