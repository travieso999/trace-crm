"use client";

import * as React from "react";

/**
 * Email HTML in a sandboxed iframe. Scripts never run (no allow-scripts); same-origin is allowed only so the
 * parent can size the frame to its content. Inbound HTML is already sanitised server-side with nh3.
 * A CSP blocks remote content (tracking pixels reveal that, and from where, a message was read) until the
 * viewer chooses to show images.
 */
export function EmailFrame({ html, title, autoHeight }: { html: string; title: string; autoHeight?: boolean }) {
  const ref = React.useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = React.useState<number | null>(null);
  const [showImages, setShowImages] = React.useState(false);
  const hasRemote = /<img[^>]+src=["']?https?:/i.test(html);
  const csp = `default-src 'none'; style-src 'unsafe-inline'; img-src data: cid:${showImages ? " https: http:" : ""}; form-action 'none'; base-uri 'none'`;
  const doc = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}"><base target="_blank"><style>html,body{margin:0}body{padding:12px;color:#0f0f0d;background:#fff;font-family:Arial,sans-serif;font-size:14px;line-height:1.5;overflow-wrap:anywhere}a{color:#b8401f}img{max-width:100%;height:auto}blockquote{margin:0 0 0 8px;padding-left:8px;border-left:2px solid #e1ddd2;color:#66625a}</style></head><body>${html}</body></html>`;
  return (
    <div className="grid gap-1">
      {hasRemote && !showImages && (
        <p className="text-xs text-muted-foreground">
          Remote images are blocked so the sender can’t tell you opened this.{" "}
          <button type="button" className="text-signal hover:underline" onClick={() => setShowImages(true)}>
            Show images
          </button>
        </p>
      )}
      <iframe
        ref={ref}
        title={title}
        sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
        srcDoc={doc}
        onLoad={() => {
          if (!autoHeight) return;
          const h = ref.current?.contentDocument?.documentElement.scrollHeight;
          if (h) setHeight(Math.min(h + 2, 1200));
        }}
        style={autoHeight && height ? { height } : undefined}
        className={autoHeight ? "h-40 w-full rounded-md border bg-white" : "h-72 w-full rounded-md border bg-white"}
      />
    </div>
  );
}
