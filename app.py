import os
import re
import shutil
import subprocess
import urllib.request
from urllib.parse import urlparse

from flask import Flask, request, jsonify, Response, send_from_directory, stream_with_context
import yt_dlp

app = Flask(__name__, static_folder='.', static_url_path='')

# ---------------------------------------------------------------------------
# Which domains belong to which platform. Kept in sync with PLATFORM_PATTERNS
# in script.js. Server-side validation always happens too — the client-side
# check can be bypassed, but the server is the real gatekeeper.
# ---------------------------------------------------------------------------
PLATFORM_DOMAINS = {
    'youtube':   ['youtube.com', 'youtu.be', 'youtube-nocookie.com'],
    'instagram': ['instagram.com'],
    'facebook':  ['facebook.com', 'fb.watch'],
    'tiktok':    ['tiktok.com', 'vm.tiktok.com'],
    'pinterest': ['pinterest.com', 'pin.it'],
}

FFMPEG_AVAILABLE = shutil.which('ffmpeg') is not None

# Instagram (and sometimes Facebook/TikTok) increasingly block anonymous,
# not-logged-in requests for private-ish content like Reels. If a plain
# request fails with a login/empty-response style error, we retry using
# cookies — either from a cookies.txt file placed next to app.py (most
# reliable, works everywhere), or pulled straight from a browser installed
# on this machine where the person is already logged in.
COOKIE_FILE = 'cookies.txt'
BROWSER_FALLBACKS = ['chrome', 'edge', 'firefox', 'brave']

AUTH_ERROR_HINTS = ('empty media response', 'login', 'private', 'rate-limit', 'restricted', 'cookies', 'sign in')

ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*m')


def clean_error(msg):
    """Strip ANSI color codes yt-dlp sometimes embeds in its error strings."""
    return ANSI_ESCAPE_RE.sub('', str(msg)).strip()


def run_with_cookie_fallback(extractor_fn, base_opts, video_url):
    """Try a plain (cookie-less) request first. If it fails in a way that
    looks like the platform is demanding a logged-in session, retry with
    cookies.txt if present, then with cookies pulled from common browsers."""
    try:
        return extractor_fn(dict(base_opts), video_url)
    except yt_dlp.utils.DownloadError as e:
        if not any(hint in str(e).lower() for hint in AUTH_ERROR_HINTS):
            raise
        last_err = e

    candidates = []
    if os.path.exists(COOKIE_FILE):
        candidates.append({'cookiefile': COOKIE_FILE})
    for browser in BROWSER_FALLBACKS:
        candidates.append({'cookiesfrombrowser': (browser,)})

    for extra in candidates:
        try:
            opts = dict(base_opts)
            opts.update(extra)
            return extractor_fn(opts, video_url)
        except Exception as e2:
            last_err = e2
            continue

    raise last_err


def hostname_matches(hostname, domains):
    hostname = hostname.lower()
    return any(hostname == d or hostname.endswith('.' + d) for d in domains)


def detect_platform(hostname):
    for key, domains in PLATFORM_DOMAINS.items():
        if hostname_matches(hostname, domains):
            return key
    return None


def safe_filename(name):
    name = re.sub(r'[\\/:*?"<>|\r\n]+', '_', name or 'download')
    return name.strip()[:120] or 'download'


def validate_url_and_platform(video_url, requested_platform):
    try:
        parsed = urlparse(video_url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('not a url')
    except Exception:
        return None, (jsonify({'status': 'error', 'message': 'That is not a valid URL.'}), 400)

    hostname = parsed.hostname or ''
    if hostname.startswith('www.'):
        hostname = hostname[4:]

    actual_platform = detect_platform(hostname)

    if requested_platform:
        if requested_platform not in PLATFORM_DOMAINS:
            return actual_platform, (jsonify({'status': 'error', 'message': f'Unknown platform "{requested_platform}".'}), 400)
        if actual_platform != requested_platform:
            wanted = requested_platform.capitalize()
            if actual_platform:
                got = actual_platform.capitalize()
                msg = f'This is the {wanted} downloader, but that link is from {got}. Use the {got} downloader page for it.'
            else:
                msg = f'This is the {wanted} downloader. Please paste a valid {wanted} link.'
            return actual_platform, (jsonify({'status': 'error', 'message': msg}), 400)
    else:
        if not actual_platform:
            msg = f'"{hostname}" is not a supported site. GrabVault only supports YouTube, Instagram, Facebook, TikTok, and Pinterest.'
            return actual_platform, (jsonify({'status': 'error', 'message': msg}), 400)

    return actual_platform, None


def headers_to_ffmpeg_arg(headers):
    if not headers:
        return None
    return ''.join(f'{k}: {v}\r\n' for k, v in headers.items())


@app.route('/')
def home():
    return send_from_directory('.', 'index.html')


@app.route('/fetch-info', methods=['POST'])
def fetch_info():
    """Fast, download-free probe: returns title, thumbnail, and the
    available quality/audio options within a couple of seconds — nothing
    is downloaded at this stage."""
    data = request.get_json(silent=True) or {}
    video_url = (data.get('url') or '').strip()
    requested_platform = (data.get('platform') or '').strip().lower() or None

    if not video_url:
        return jsonify({'status': 'error', 'message': 'No URL link provided!'}), 400

    _, error = validate_url_and_platform(video_url, requested_platform)
    if error:
        return error

    ydl_opts = {'quiet': True, 'no_warnings': True, 'noplaylist': True, 'skip_download': True}

    def do_extract(opts, url):
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = run_with_cookie_fallback(do_extract, ydl_opts, video_url)

        formats = info.get('formats') or []
        available_heights = sorted(
            {f.get('height') for f in formats if f.get('vcodec') not in (None, 'none') and f.get('height')},
            reverse=True
        )

        # Only ever offer a quality option that actually exists as a real
        # format for THIS video. Platforms like YouTube usually have a full
        # ladder (144p..1080p); Instagram/TikTok/Facebook/Pinterest often
        # only have ONE native resolution — in that case we show exactly
        # that one option instead of a fake list, since selecting a
        # resolution that doesn't exist is what was causing download
        # errors before.
        qualities = []
        if available_heights:
            for h in available_heights[:6]:
                match = next((f for f in formats if f.get('height') == h), None)
                filesize = (match or {}).get('filesize') or (match or {}).get('filesize_approx')
                qualities.append({'height': h, 'label': f'{h}P', 'filesize': filesize})
        else:
            # No height info at all (rare) — fall back to a single
            # "best available" option so downloading still works.
            qualities.append({'height': 0, 'label': 'Best available', 'filesize': None})

        audio_formats = [f for f in formats if f.get('vcodec') in (None, 'none') and f.get('acodec') not in (None, 'none')]
        audio_filesize = None
        if audio_formats:
            best_audio = max(audio_formats, key=lambda f: f.get('abr') or 0)
            audio_filesize = best_audio.get('filesize') or best_audio.get('filesize_approx')

        return jsonify({
            'status': 'success',
            'title': info.get('title', 'Untitled video'),
            'thumbnail': info.get('thumbnail'),
            'uploader': info.get('uploader'),
            'duration': info.get('duration'),
            'qualities': qualities,
            'audio_filesize': audio_filesize,
            'audio_available': bool(audio_formats) or FFMPEG_AVAILABLE,
        })

    except yt_dlp.utils.DownloadError as e:
        return jsonify({'status': 'error', 'message': f'Could not read this link: {clean_error(e)[:220]}'}), 502
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Unexpected error: {clean_error(e)[:220]}'}), 500


def resolve_format(video_url, format_selector):
    ydl_opts = {'format': format_selector, 'quiet': True, 'no_warnings': True, 'noplaylist': True}

    def do_extract(opts, url):
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    return run_with_cookie_fallback(do_extract, ydl_opts, video_url)


def stream_process(cmd):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1024 * 1024)

    def generate():
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            proc.terminate()

    return generate()


def stream_url(src_url, headers):
    req = urllib.request.Request(src_url, headers=headers or {})
    resp = urllib.request.urlopen(req, timeout=60)

    def generate():
        try:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            resp.close()

    return generate(), resp.headers.get('Content-Length')


@app.route('/download', methods=['GET'])
def download_video():
    """Streams the file straight through to the browser as it comes in —
    no waiting for a full server-side download first. Chrome/Edge/Firefox
    pick this up in their own download manager the moment bytes start
    arriving, the same way any normal file link works."""
    video_url = (request.args.get('url') or '').strip()
    requested_platform = (request.args.get('platform') or '').strip().lower() or None
    media_type = (request.args.get('type') or 'video').strip().lower()
    quality = request.args.get('quality')

    if not video_url:
        return jsonify({'status': 'error', 'message': 'No URL link provided!'}), 400

    _, error = validate_url_and_platform(video_url, requested_platform)
    if error:
        return error

    try:
        if media_type == 'audio':
            if not FFMPEG_AVAILABLE:
                return jsonify({'status': 'error', 'message': 'Audio download needs ffmpeg installed on the server.'}), 500

            info = resolve_format(video_url, 'bestaudio/best')
            title = safe_filename(info.get('title'))
            headers_arg = headers_to_ffmpeg_arg(info.get('http_headers'))

            cmd = ['ffmpeg', '-loglevel', 'error']
            if headers_arg:
                cmd += ['-headers', headers_arg]
            cmd += ['-i', info['url'], '-vn', '-acodec', 'libmp3lame', '-b:a', '192k', '-f', 'mp3', 'pipe:1']

            return Response(
                stream_with_context(stream_process(cmd)),
                mimetype='audio/mpeg',
                headers={'Content-Disposition': f'attachment; filename="{title}.mp3"'}
            )

        try:
            height = int(quality)
        except (TypeError, ValueError):
            height = 720

        if height <= 0:
            fmt_selector = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
            filename_quality = 'best'
        else:
            fmt_selector = f'bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={height}]/best'
            filename_quality = f'{height}p'

        info = resolve_format(video_url, fmt_selector)
        title = safe_filename(info.get('title'))
        filename = f'{title}-{filename_quality}.mp4'

        requested_formats = info.get('requested_formats')
        if requested_formats and len(requested_formats) >= 2:
            if not FFMPEG_AVAILABLE:
                return jsonify({'status': 'error', 'message': 'This quality needs merging video+audio, which requires ffmpeg installed on the server.'}), 500

            video_f = next((f for f in requested_formats if f.get('vcodec') not in (None, 'none')), requested_formats[0])
            audio_f = next((f for f in requested_formats if f.get('acodec') not in (None, 'none') and f.get('vcodec') in (None, 'none')), requested_formats[-1])

            cmd = ['ffmpeg', '-loglevel', 'error']
            v_headers = headers_to_ffmpeg_arg(video_f.get('http_headers'))
            if v_headers:
                cmd += ['-headers', v_headers]
            cmd += ['-i', video_f['url']]
            a_headers = headers_to_ffmpeg_arg(audio_f.get('http_headers'))
            if a_headers:
                cmd += ['-headers', a_headers]
            cmd += ['-i', audio_f['url'], '-c', 'copy', '-movflags', 'frag_keyframe+empty_moov+default_base_moof', '-f', 'mp4', 'pipe:1']

            return Response(
                stream_with_context(stream_process(cmd)),
                mimetype='video/mp4',
                headers={'Content-Disposition': f'attachment; filename="{filename}"'}
            )

        # Progressive single-file stream — no merge needed, proxy it directly
        generate, content_length = stream_url(info['url'], info.get('http_headers'))
        resp_headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
        if content_length:
            resp_headers['Content-Length'] = content_length
        return Response(stream_with_context(generate), mimetype='video/mp4', headers=resp_headers)

    except yt_dlp.utils.DownloadError as e:
        return jsonify({'status': 'error', 'message': f'Could not download this link: {clean_error(e)[:220]}'}), 502
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Unexpected error: {clean_error(e)[:220]}'}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000, threaded=True)
