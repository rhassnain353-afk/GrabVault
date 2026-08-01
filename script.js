/* ==========================================================================
   GrabVault — shared behaviors & backend integration
   ========================================================================== */

// ---- Theme toggle (persisted) ----
(function themeInit() {
  const saved = localStorage.getItem('gv-theme');
  if (saved === 'light') document.documentElement.setAttribute('data-theme', 'light');
})();

function setupThemeToggle() {
  const btn = document.querySelector('.theme-toggle');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    if (isLight) {
      document.documentElement.removeAttribute('data-theme');
      localStorage.setItem('gv-theme', 'dark');
    } else {
      document.documentElement.setAttribute('data-theme', 'light');
      localStorage.setItem('gv-theme', 'light');
    }
  });
}

// ---- Mobile nav drawer ----
function setupMobileNav() {
  const toggleBtn = document.querySelector('.nav-toggle-btn');
  const drawer = document.querySelector('.mobile-drawer');
  if (!toggleBtn || !drawer) return;
  toggleBtn.addEventListener('click', () => drawer.classList.toggle('open'));
}

// ---- Typewriter cycling text ----
function setupTypewriter(el, words, opts = {}) {
  if (!el) return;
  const typeSpeed = opts.typeSpeed || 55;
  const eraseSpeed = opts.eraseSpeed || 30;
  const holdTime = opts.holdTime || 1400;
  let wordIndex = 0, charIndex = 0, deleting = false;

  function tick() {
    const word = words[wordIndex];
    if (!deleting) {
      charIndex++;
      el.textContent = word.slice(0, charIndex);
      if (charIndex === word.length) {
        deleting = true;
        setTimeout(tick, holdTime);
        return;
      }
      setTimeout(tick, typeSpeed);
    } else {
      charIndex--;
      el.textContent = word.slice(0, charIndex);
      if (charIndex === 0) {
        deleting = false;
        wordIndex = (wordIndex + 1) % words.length;
      }
      setTimeout(tick, eraseSpeed);
    }
  }
  tick();
}

// ---- 3D tilt on hover ----
function setupTilt() {
  const cards = document.querySelectorAll('.tilt-card');
  cards.forEach((card) => {
    card.addEventListener('mousemove', (e) => {
      const rect = card.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const rotateX = ((y / rect.height) - 0.5) * -12;
      const rotateY = ((x / rect.width) - 0.5) * 12;
      card.style.transform = `perspective(800px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) translateZ(0)`;
    });
    card.addEventListener('mouseleave', () => {
      card.style.transform = 'perspective(800px) rotateX(0deg) rotateY(0deg)';
    });
  });
}

// ---- FAQ accordion ----
function setupFaq() {
  document.querySelectorAll('.faq-item').forEach((item) => {
    const q = item.querySelector('.faq-q');
    if (!q) return;
    q.addEventListener('click', () => {
      const wasOpen = item.classList.contains('open');
      document.querySelectorAll('.faq-item.open').forEach((i) => i.classList.remove('open'));
      if (!wasOpen) item.classList.add('open');
    });
  });
}

// ---- Scroll reveal ----
function setupReveal() {
  const targets = document.querySelectorAll('[data-reveal]');
  if (!targets.length) return;
  const io = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('reveal');
        io.unobserve(entry.target);
      }
    });
  }, { threshold: 0.15 });
  targets.forEach((t) => io.observe(t));
}

// ---- Per-platform URL validation ----
// Each platform page only accepts links from its own domain(s).
// The home page (no platformKey) accepts a link from any supported platform.
const PLATFORM_PATTERNS = {
  youtube:   { label: 'YouTube',   regex: /(?:^|\.)(?:youtube\.com|youtube-nocookie\.com|youtu\.be)$/i },
  instagram: { label: 'Instagram', regex: /(?:^|\.)instagram\.com$/i },
  facebook:  { label: 'Facebook',  regex: /(?:^|\.)(?:facebook\.com|fb\.watch)$/i },
  tiktok:    { label: 'TikTok',    regex: /(?:^|\.)(?:tiktok\.com|vm\.tiktok\.com)$/i },
  pinterest: { label: 'Pinterest', regex: /(?:^|\.)(?:pinterest\.[a-z.]+|pin\.it)$/i },
};

function detectPlatform(hostname) {
  for (const key in PLATFORM_PATTERNS) {
    if (PLATFORM_PATTERNS[key].regex.test(hostname)) return key;
  }
  return null;
}

// ---- Real Backend Download Integration ----
// platformKey: one of 'youtube' | 'instagram' | 'facebook' | 'tiktok' | 'pinterest' | null (any, used on the home page)
function setupGrabForm(platformLabel, platformKey = null) {
  const form = document.querySelector('[data-grab-form]') || document.querySelector('form');
  if (!form) return;

  // Guard against attaching the listener twice to the same form
  if (form.dataset.grabBound === 'true') return;
  form.dataset.grabBound = 'true';

  const input = form.querySelector('input');
  const status = document.querySelector('[data-grab-status]');
  const submitBtn = form.querySelector('button[type="submit"]');

  // Results panel — created once, right after the search box, and reused
  let resultBox = document.querySelector('[data-grab-result]');
  if (!resultBox) {
    resultBox = document.createElement('div');
    resultBox.className = 'grab-result';
    resultBox.setAttribute('data-grab-result', '');
    form.insertAdjacentElement('afterend', resultBox);
  }

  function clearResult() {
    resultBox.innerHTML = '';
    resultBox.classList.remove('is-visible');
  }

  function sizeLabel(bytes) {
    if (!bytes || bytes <= 0) return '';
    const mb = bytes / (1024 * 1024);
    return mb >= 1 ? `${mb.toFixed(1)} MB` : `${(bytes / 1024).toFixed(0)} KB`;
  }

  function getDownloadFrame() {
    let iframe = document.getElementById('gv-download-frame');
    if (!iframe) {
      iframe = document.createElement('iframe');
      iframe.id = 'gv-download-frame';
      iframe.style.display = 'none';
      document.body.appendChild(iframe);
    }
    return iframe;
  }

  function startDownload(url, platform, opts, btnEl) {
    const original = btnEl.textContent;
    btnEl.disabled = true;
    btnEl.textContent = 'Starting…';

    const params = new URLSearchParams({ url, platform: platform || '', ...opts });
    const dlUrl = `/download?${params.toString()}`;

    // Point a hidden iframe at the streaming endpoint. As soon as the
    // server starts sending bytes (an "attachment" response), the browser's
    // own download manager takes over and shows progress immediately —
    // there's no JSON round trip or full server-side download to wait for.
    getDownloadFrame().src = dlUrl;

    setTimeout(() => {
      btnEl.textContent = original;
      btnEl.disabled = false;
    }, 2500);
  }

  function renderResult(info, url, platform) {
    resultBox.innerHTML = '';

    const card = document.createElement('div');
    card.className = 'grab-card';

    // Left: thumbnail + title
    const media = document.createElement('div');
    media.className = 'grab-media';
    media.innerHTML = `
      ${info.thumbnail ? `<img src="${info.thumbnail}" alt="" loading="lazy">` : `<div class="grab-thumb-fallback">🎬</div>`}
      <p class="grab-title">${(info.title || 'Untitled video').replace(/</g, '&lt;')}</p>
      ${info.uploader ? `<p class="grab-uploader">${info.uploader.replace(/</g, '&lt;')}</p>` : ''}
    `;

    // Right: quality + audio rows
    const list = document.createElement('div');
    list.className = 'grab-formats';

    const rowsHeader = document.createElement('div');
    rowsHeader.className = 'grab-formats-head';
    rowsHeader.innerHTML = `<span>▶️ Video</span>`;
    list.appendChild(rowsHeader);

    (info.qualities || []).forEach((q) => {
      const row = document.createElement('div');
      row.className = 'quality-row';
      row.innerHTML = `
        <span class="quality-badge">MP4</span>
        <span class="quality-res">${q.label}</span>
        <span class="quality-size">${sizeLabel(q.filesize) || 'size varies'}</span>
      `;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn quality-btn';
      btn.textContent = '↓ Download';
      btn.addEventListener('click', () => startDownload(url, platform, { type: 'video', quality: q.height }, btn));
      row.appendChild(btn);
      list.appendChild(row);
    });

    const audioHead = document.createElement('div');
    audioHead.className = 'grab-formats-head';
    audioHead.innerHTML = `<span>🎧 Audio only</span>`;
    list.appendChild(audioHead);

    const audioRow = document.createElement('div');
    audioRow.className = 'quality-row';
    audioRow.innerHTML = `
      <span class="quality-badge audio">MP3</span>
      <span class="quality-res">Audio</span>
      <span class="quality-size">${sizeLabel(info.audio_filesize) || 'size varies'}</span>
    `;
    const audioBtn = document.createElement('button');
    audioBtn.type = 'button';
    audioBtn.className = 'btn quality-btn';
    audioBtn.textContent = '↓ Download';
    audioBtn.addEventListener('click', () => startDownload(url, platform, { type: 'audio' }, audioBtn));
    audioRow.appendChild(audioBtn);
    list.appendChild(audioRow);

    card.appendChild(media);
    card.appendChild(list);
    resultBox.appendChild(card);
    resultBox.classList.add('is-visible');
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!input) return;

    const url = input.value.trim();
    clearResult();

    if (!url) {
      if (status) status.textContent = 'Please paste a link first!';
      return;
    }

    // Basic URL shape check
    let parsed;
    try {
      parsed = new URL(url);
      if (!/^https?:$/.test(parsed.protocol)) throw new Error('bad protocol');
    } catch {
      if (status) status.textContent = 'That doesn\'t look like a valid link. Please paste a full URL (starting with http:// or https://).';
      return;
    }

    const hostname = parsed.hostname.replace(/^www\./, '');
    const matchedPlatform = detectPlatform(hostname);

    if (platformKey) {
      // Platform-specific page: only that platform's links are accepted
      if (matchedPlatform !== platformKey) {
        const wanted = PLATFORM_PATTERNS[platformKey].label;
        if (status) {
          status.textContent = matchedPlatform
            ? `This is the ${wanted} downloader — that link is from ${PLATFORM_PATTERNS[matchedPlatform].label}. Please use the ${PLATFORM_PATTERNS[matchedPlatform].label} downloader page instead.`
            : `This is the ${wanted} downloader. Please paste a valid ${wanted} link.`;
        }
        return;
      }
    } else {
      // Home page: any supported platform is accepted, anything else is rejected
      if (!matchedPlatform) {
        if (status) status.textContent = `Sorry, "${hostname}" isn't a supported site yet. GrabVault only supports YouTube, Instagram, Facebook, TikTok and Pinterest links.`;
        return;
      }
    }

    const platform = platformKey || matchedPlatform;
    if (status) status.textContent = `Fetching ${platformLabel || 'video'} info…`;
    if (submitBtn) submitBtn.disabled = true;

    try {
      const response = await fetch('/fetch-info', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, platform })
      });

      let info;
      try { info = await response.json(); } catch { throw new Error('bad response'); }

      if (response.ok && info.status === 'success') {
        if (status) status.textContent = '';
        renderResult(info, url, platform);
      } else {
        if (status) status.textContent = `Error: ${info.message || 'Could not fetch that link.'}`;
      }
    } catch (error) {
      console.error('Error:', error);
      if (status) status.textContent = 'Could not connect to the server! Make sure app.py is running and you opened this page via http://localhost:5000 (not by double-clicking the HTML file).';
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });
}

// ---- DOM Ready Listener ----
document.addEventListener('DOMContentLoaded', () => {
  setupThemeToggle();
  setupMobileNav();
  setupTilt();
  setupFaq();
  setupReveal();
  // NOTE: setupGrabForm is called from the inline <script> at the bottom of
  // each page (with the correct platform key). We do NOT call it again here —
  // doing so used to attach a second submit listener to the same form and
  // fire the download request twice.
});