(async () => {
  'use strict';

  // Run only in DevTools on the conversation you want to export at chatgpt.com.
  // GET requests only; it never deletes anything from your ChatGPT account.

  const INCLUDE_BINARY_AS_BASE64 = true; // preserve images/binary files if no plain text is extractable

  const id = location.pathname.match(/\/c\/([0-9a-f-]{32,36})/i)?.[1];

  if (location.hostname !== 'chatgpt.com' || !id) {
    throw new Error('Open the conversation URL at https://chatgpt.com/c/<conversation-id> first.');
  }

  let session = {};

  try {
    const r = await fetch('/api/auth/session', {credentials: 'include'});
    if (r.ok) session = await r.json();
  } catch (_) {}

  if (!session.accessToken) {
    try {
      session = JSON.parse(
        document.querySelector('#client-bootstrap')?.textContent || '{}'
      ).session || {};
    } catch (_) {}
  }

  const headers = {Accept: 'application/json'};

  if (session.accessToken) {
    headers.Authorization = `Bearer ${session.accessToken}`;
  }

  if (session.account?.id) {
    headers['ChatGPT-Account-Id'] = session.account.id;
  }

  const sleep = ms => new Promise(r => setTimeout(r, ms));

  async function api(path) {
    const r = await fetch(path, {
      headers,
      credentials: 'include'
    });

    if (!r.ok) {
      throw new Error(`GET ${path} failed: ${r.status} ${r.statusText}`);
    }

    return r;
  }

  console.log('[Chat export] Reading complete conversation...');

  const raw = await (
    await api(`/backend-api/conversation/${encodeURIComponent(id)}`)
  ).json();

  const conv = raw.mapping ? raw : raw.conversation;

  if (!conv?.mapping) {
    throw new Error(
      'Conversation API response has no mapping. No partial export was created.'
    );
  }

  const mapping = conv.mapping;
  const route = [];

  let at = conv.current_node || raw.current_node;

  const seen = new Set();

  while (at && mapping[at] && !seen.has(at)) {
    seen.add(at);
    route.push(mapping[at]);
    at = mapping[at].parent;
  }

  route.reverse();

  const nodes = route.length
    ? route
    : Object.values(mapping).sort(
        (a, b) =>
          (a.message?.create_time || 0) -
          (b.message?.create_time || 0)
      );

  const decoder = new TextDecoder();

  const nameOf = x =>
    x?.name || x?.file_name || x?.filename || '';

  const typeOf = x =>
    x?.mime_type || x?.mimeType || x?.type || '';

  const cleanId = x =>
    String(x || '').replace(/^(file-service|sediment):\/\//, '');

  const partText = p =>
    typeof p === 'string'
      ? p
      : (
          typeof p?.text === 'string'
            ? p.text
            : typeof p?.transcription === 'string'
              ? p.transcription
              : ''
        );

  function attachmentsOf(m) {
    const found = new Map();

    function add(a) {
      const fid = cleanId(
        a?.id ||
        a?.file_id ||
        a?.asset_pointer ||
        a?.assetPointer
      );

      if (!fid || !/^file[-_]/i.test(fid)) return;

      const old = found.get(fid) || {};

      found.set(fid, {
        id: fid,
        name: nameOf(a) || old.name || `${fid}.bin`,
        mime: typeOf(a) || old.mime || '',
        url: a?.download_url || old.url || ''
      });
    }

    for (const a of m.metadata?.attachments || []) {
      add(a);
    }

    function scan(v) {
      if (!v || typeof v !== 'object') return;

      if (v.asset_pointer || v.file_id) {
        add(v);
      }

      if (Array.isArray(v)) {
        for (const z of v) scan(z);
      } else {
        for (const [k, z] of Object.entries(v)) {
          if (
            ![
              'text',
              'transcription',
              'image_url',
              'thumbnail_url'
            ].includes(k) &&
            typeof z === 'object'
          ) {
            scan(z);
          }
        }
      }
    }

    scan(m.content?.parts || []);

    return [...found.values()];
  }

  async function fetchURL(u) {
    const url = new URL(u, location.origin);

    if (!['http:', 'https:'].includes(url.protocol)) {
      throw new Error('Unsupported download scheme');
    }

    const local = url.origin === location.origin;

    const r = await fetch(url.href, {
      headers: local ? headers : {},
      credentials: local ? 'include' : 'omit'
    });

    if (!r.ok) {
      throw new Error(`Download returned HTTP ${r.status}`);
    }

    return r.blob();
  }

  async function fileBlob(f) {
    const fid = encodeURIComponent(f.id);

    const paths = [
      `/backend-api/files/download/${fid}?conversation_id=${encodeURIComponent(id)}&inline=false`,
      `/backend-api/files/${fid}/download`,
      `/backend-api/conversation/${encodeURIComponent(id)}/attachment/${fid}/download`
    ];

    const failures = [];

    for (const path of paths) {
      try {
        const response = await api(path);

        if (/json/i.test(response.headers.get('content-type') || '')) {
          const v = await response.json();

          const url =
            v.download_url ||
            v.url ||
            v.content_url ||
            v.data?.download_url;

          if (!url) {
            throw new Error('No download URL in API response');
          }

          return await fetchURL(url);
        }

        return await response.blob();

      } catch (e) {
        failures.push(e.message);
      }
    }

    if (f.url) {
      try {
        return await fetchURL(f.url);
      } catch (e) {
        failures.push(e.message);
      }
    }

    throw new Error(failures.join(' | '));
  }

  function plainText(blob, bytes) {
    if (bytes[0] === 0xff && bytes[1] === 0xfe) {
      return new TextDecoder('utf-16le').decode(bytes);
    }

    if (bytes[0] === 0xfe && bytes[1] === 0xff) {
      return new TextDecoder('utf-16be').decode(bytes);
    }

    let out;

    try {
      out = new TextDecoder('utf-8', {fatal: true}).decode(bytes);
    } catch (_) {
      out = new TextDecoder('windows-1252').decode(bytes);
    }

    if (out.includes('\0')) {
      throw new Error('Binary data, not a readable text file');
    }

    return out;
  }

  function isText(f) {
    return /^(text\/|application\/(json|xml|javascript|x-ndjson|yaml|sql|graphql))/i.test(f.mime) ||
      /\.(txt|md|markdown|json|jsonl|ndjson|xml|yaml|yml|csv|tsv|log|py|js|jsx|mjs|cjs|ts|tsx|html?|css|scss|java|kt|go|rs|rb|php|sh|bash|zsh|ps1|bat|cmd|c|cc|cpp|h|hpp|cs|sql|toml|ini|conf|cfg|env|gitignore|patch|diff|tex|ipynb|svg)$/i.test(f.name);
  }

  // Small ZIP reader for Office attachments.
  // Nothing is written to disk.

  async function unzip(blob) {
    const b = new Uint8Array(await blob.arrayBuffer());

    const dv = new DataView(
      b.buffer,
      b.byteOffset,
      b.byteLength
    );

    let end = -1;

    for (
      let p = b.length - 22;
      p >= Math.max(0, b.length - 65557);
      --p
    ) {
      if (dv.getUint32(p, true) === 0x06054b50) {
        end = p;
        break;
      }
    }

    if (end < 0) {
      throw new Error('ZIP directory not found');
    }

    let p = dv.getUint32(end + 16, true);

    const count = dv.getUint16(end + 10, true);
    const result = new Map();

    for (let i = 0; i < count; ++i) {
      if (dv.getUint32(p, true) !== 0x02014b50) {
        throw new Error('Invalid ZIP directory');
      }

      const method = dv.getUint16(p + 10, true);
      const compSize = dv.getUint32(p + 20, true);
      const nameLen = dv.getUint16(p + 28, true);

      const extra = dv.getUint16(p + 30, true);
      const comment = dv.getUint16(p + 32, true);

      const offset = dv.getUint32(p + 42, true);

      const filename = decoder.decode(
        b.subarray(p + 46, p + 46 + nameLen)
      );

      p += 46 + nameLen + extra + comment;

      if (filename.endsWith('/')) continue;

      if (dv.getUint32(offset, true) !== 0x04034b50) {
        throw new Error('Invalid ZIP entry');
      }

      const start =
        offset +
        30 +
        dv.getUint16(offset + 26, true) +
        dv.getUint16(offset + 28, true);

      const packed = b.slice(start, start + compSize);

      let content;

      if (method === 0) {
        content = packed;

      } else if (method === 8) {
        if (!globalThis.DecompressionStream) {
          throw new Error(
            'Browser lacks ZIP decompression support'
          );
        }

        const inflated = new Blob([packed])
          .stream()
          .pipeThrough(new DecompressionStream('deflate-raw'));

        content = new Uint8Array(
          await new Response(inflated).arrayBuffer()
        );

      } else {
        continue;
      }

      result.set(filename, decoder.decode(content));
    }

    return result;
  }

  const xml = s =>
    new DOMParser().parseFromString(s, 'application/xml');

  const descendants = (n, local) =>
    Array.from(n.getElementsByTagName('*'))
      .filter(e => e.localName === local);

  const joinedTexts = n =>
    descendants(n, 't')
      .map(e => e.textContent)
      .join('');

  async function officeText(blob, ext) {
    const z = await unzip(blob);

    if (ext === 'docx') {
      const names = [...z.keys()].filter(n =>
        /^word\/(document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml$/.test(n)
      );

      return names.map(n =>
        `[${n}]\n` +
        descendants(xml(z.get(n)), 'p')
          .map(p =>
            Array.from(p.getElementsByTagName('*'))
              .filter(e =>
                ['t', 'br', 'tab'].includes(e.localName)
              )
              .map(e =>
                e.localName === 'br'
                  ? '\n'
                  : e.localName === 'tab'
                    ? '\t'
                    : e.textContent
              )
              .join('')
          )
          .join('\n')
      ).join('\n\n');
    }

    if (ext === 'pptx') {
      return [...z.keys()]
        .filter(n => /^ppt\/slides\/slide\d+\.xml$/.test(n))
        .sort(
          (a, b) =>
            Number(a.match(/slide(\d+)/)[1]) -
            Number(b.match(/slide(\d+)/)[1])
        )
        .map(n =>
          `[${n}]\n` +
          descendants(xml(z.get(n)), 'p')
            .map(joinedTexts)
            .join('\n')
        )
        .join('\n\n');
    }

    if (ext === 'xlsx') {
      const shared = z.has('xl/sharedStrings.xml')
        ? descendants(
            xml(z.get('xl/sharedStrings.xml')),
            'si'
          ).map(joinedTexts)
        : [];

      return [...z.keys()]
        .filter(n => /^xl\/worksheets\/sheet\d+\.xml$/.test(n))
        .sort(
          (a, b) =>
            Number(a.match(/sheet(\d+)/)[1]) -
            Number(b.match(/sheet(\d+)/)[1])
        )
        .map(n =>
          `[${n}]\n` +
          descendants(xml(z.get(n)), 'row')
            .map(row =>
              descendants(row, 'c')
                .map(c => {
                  if (c.getAttribute('t') === 's') {
                    return shared[
                      Number(descendants(c, 'v')[0]?.textContent)
                    ] || '';
                  }

                  if (c.getAttribute('t') === 'inlineStr') {
                    return joinedTexts(c);
                  }

                  return descendants(c, 'v')[0]?.textContent || '';
                })
                .join('\t')
            )
            .join('\n')
        )
        .join('\n\n');
    }

    return [...z]
      .filter(([n]) =>
        /\.(txt|md|csv|json|xml|py|js|html|css|yaml|yml|log)$/i.test(n)
      )
      .map(([n, value]) => `[${n}]\n${value}`)
      .join('\n\n');
  }

  let pdfLibrary;

  async function pdfText(blob) {
    if (!pdfLibrary) {
      pdfLibrary = globalThis.pdfjsLib;

      if (!pdfLibrary?.getDocument) {
        throw new Error(
          'PDF.js is not already available in this tab; preserving the PDF as base64 instead (no third-party script is loaded)'
        );
      }
    }

    const pdf = await pdfLibrary.getDocument({
      data: new Uint8Array(await blob.arrayBuffer()),
      disableFontFace: true
    }).promise;

    const result = [];

    for (let i = 1; i <= pdf.numPages; i++) {
      const page = await pdf.getPage(i);

      const items = (
        await page.getTextContent()
      ).items;

      result.push(
        `[PDF page ${i}]\n` +
        items
          .map(x => x.str + (x.hasEOL ? '\n' : ' '))
          .join('')
          .trim()
      );

      page.cleanup();
    }

    return result.join('\n\n');
  }

  async function base64(blob) {
    return await new Promise((resolve, reject) => {
      const fr = new FileReader();

      fr.onload = () =>
        resolve(String(fr.result).split(',', 2)[1]);

      fr.onerror = () =>
        reject(fr.error);

      fr.readAsDataURL(blob);
    });
  }

  async function extractFile(blob, f) {
    const ext = (f.name.split('.').pop() || '').toLowerCase();

    try {
      if (isText(f)) {
        return {
          format: 'plain text',
          value: plainText(
            blob,
            new Uint8Array(await blob.arrayBuffer())
          )
        };
      }

      if (['docx', 'pptx', 'xlsx', 'zip'].includes(ext)) {
        const content = await officeText(blob, ext);

        if (content.trim()) {
          return {
            format: 'extracted text',
            value: content
          };
        }

        throw new Error('No textual ZIP entries found');
      }

      if (ext === 'pdf' || f.mime === 'application/pdf') {
        const content = await pdfText(blob);

        if (content.trim()) {
          return {
            format: 'PDF extracted text',
            value: content
          };
        }

        throw new Error(
          'No extractable PDF text (perhaps scanned)'
        );
      }

      throw new Error(
        'No text extractor for this file type'
      );

    } catch (e) {
      if (!INCLUDE_BINARY_AS_BASE64) {
        return {
          format: 'not extracted',
          value: String(e.message)
        };
      }

      return {
        format: `base64 fallback; not human-readable (${e.message})`,
        value: await base64(blob)
      };
    }
  }

  // Build final raw-text conversation.

  const out = [
    `TITLE: ${conv.title || raw.title || document.title}`,
    `SOURCE: ${location.href}`,
    `EXPORTED: ${new Date().toISOString()}`,
    'Conversation: active message branch, chronological; attachment content is embedded under its message.',
    '='.repeat(88),
    ''
  ];

  let number = 0;
  let fileCount = 0;
  let failures = 0;
  let fallbacks = 0;

  const cached = new Map();

  for (const node of nodes) {
    const m = node.message;

    if (
      !m ||
      m.metadata?.is_visually_hidden_from_conversation
    ) {
      continue;
    }

    const role = m.author?.role;

    if (!['user', 'assistant'].includes(role)) {
      continue;
    }

    if (
      role === 'assistant' &&
      m.recipient &&
      m.recipient !== 'all'
    ) {
      continue;
    }

    const text = (m.content?.parts || [])
      .map(partText)
      .filter(Boolean)
      .join('\n');

    const files = attachmentsOf(m);

    if (!text.trim() && !files.length) {
      continue;
    }

    out.push(
      `[${String(++number).padStart(4, '0')}] ${role.toUpperCase()}${
        m.create_time
          ? ' | ' + new Date(m.create_time * 1000).toISOString()
          : ''
      }`,
      ''
    );

    if (text) {
      out.push(text, '');
    }

    for (const f of files) {
      fileCount++;

      console.log(
        `[Chat export] Attachment ${fileCount}: ${f.name}`
      );

      out.push(
        `----- BEGIN INLINE ATTACHMENT: ${f.name} (${f.mime || 'unknown MIME'}) -----`
      );

      try {
        let result = cached.get(f.id);

        if (!result) {
          // In-memory fetch; NOT saved as another download.

          let blob = await fileBlob(f);

          result = await extractFile(blob, f);

          cached.set(f.id, result);

          // Release original bytes.
          // Only converted text is retained.

          blob = null;
        }

        if (result.format.startsWith('base64')) {
          fallbacks++;
        }

        out.push(
          `[Conversion: ${result.format}]`,
          result.value
        );

      } catch (e) {
        failures++;

        out.push(
          `[ATTACHMENT DOWNLOAD FAILED: ${String(e.message)}]`
        );

        console.warn(
          `[Chat export] Failed: ${f.name}`,
          e
        );
      }

      out.push(
        `----- END INLINE ATTACHMENT: ${f.name} -----`,
        ''
      );

      await sleep(100);
    }

    out.push(
      '-'.repeat(88),
      ''
    );
  }

  out.push(
    `EXPORT SUMMARY: ${number} messages; ${fileCount} attachment references; ${failures} download failures; ${fallbacks} base64 fallbacks.`
  );

  // Download exactly one final TXT file.

  const txtBlob = new Blob(
    [out.join('\n')],
    {
      type: 'text/plain;charset=utf-8'
    }
  );

  const url = URL.createObjectURL(txtBlob);

  const a = document.createElement('a');

  a.href = url;

  a.download = `${
    (conv.title || raw.title || 'chatgpt-conversation')
      .replace(/[\\/:*?"<>|]/g, '_')
      .slice(0, 80)
  }-full-inline.txt`;

  document.body.appendChild(a);

  a.click();

  a.remove();

  setTimeout(
    () => URL.revokeObjectURL(url),
    60000
  );

  cached.clear();

  // No separate attachment files are ever left in Downloads.

  console.log(
    `[Chat export] Download triggered: ${a.download}. ${number} messages, ${fileCount} attachments, ${failures} failures, ${fallbacks} base64 fallbacks.`
  );

})().catch(error =>
  console.error('[Chat export] STOPPED:', error)
);