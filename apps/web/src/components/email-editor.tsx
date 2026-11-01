"use client";

import * as React from "react";
import Document from "@tiptap/extension-document";
import HardBreak from "@tiptap/extension-hard-break";
import Link from "@tiptap/extension-link";
import Paragraph from "@tiptap/extension-paragraph";
import Text from "@tiptap/extension-text";
import { UndoRedo } from "@tiptap/extensions";
import { Fragment, Slice } from "@tiptap/pm/model";
import { EditorContent, useEditor, useEditorState, type Editor, type JSONContent } from "@tiptap/react";
import { ExternalLink, Link2, PenLine, Pencil, Unlink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/common";

/* The stored body stays plain text, so templates, variables and the plain-text part work as before: each line is a
   paragraph here, and a link on words is [label](url). A bare address is a link whose words are the address. */
const LINK_OR_URL = /\[([^[\]\n]+)\]\(\s*((?:https?:\/\/|mailto:|tel:|(?=\{\{))(?:\{\{[^{}]*\}\}|[^\s()<>[\]{}])*)\s*\)|https?:\/\/[^\s<>"']+/g;
const SIGNATURE = /\{\{\s*signature\s*(?:\|[^{}]*)?\}\}/;

function textNode(text: string, href?: string): JSONContent {
  return href ? { type: "text", text, marks: [{ type: "link", attrs: { href } }] } : { type: "text", text };
}

function lineToNodes(line: string): JSONContent[] {
  const nodes: JSONContent[] = [];
  let pos = 0;
  for (const m of line.matchAll(LINK_OR_URL)) {
    if (m.index > pos) nodes.push(textNode(line.slice(pos, m.index)));
    nodes.push(m[1] ? textNode(m[1], m[2]) : textNode(m[0], m[0]));
    pos = m.index + m[0].length;
  }
  if (pos < line.length) nodes.push(textNode(line.slice(pos)));
  return nodes;
}

export function textToDoc(text: string): JSONContent {
  return { type: "doc", content: text.split(/\r?\n/).map((line) => ({ type: "paragraph", content: lineToNodes(line) })) };
}

/** The characters [label](url) can't hold in an address; placeholders are left whole. */
function encodeHref(href: string) {
  return href.split(/(\{\{[^{}]*\}\})/).map((part, i) =>
    i % 2 ? part : part.replace(/[\s()<>[\]{}]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase().padStart(2, "0")}`),
  ).join("");
}

function nodeToText(node: JSONContent): string {
  if (node.type === "hardBreak") return "\n";
  const text = node.text ?? "";
  const href = node.marks?.find((m) => m.type === "link")?.attrs?.href as string | undefined;
  if (!href || text === href) return text;
  return `[${text.replace(/[[\]]/g, "")}](${encodeHref(href)})`;
}

export function docToText(doc: JSONContent): string {
  return (doc.content ?? []).map((p) => (p.content ?? []).map(nodeToText).join("")).join("\n");
}

/** What was typed in the address box as a link, or null if it can't be one. */
function toHref(input: string): string | null {
  const v = input.trim();
  if (!v) return null;
  if (/^\{\{[^{}]+\}\}$/.test(v)) return v;
  if (/^(https?:\/\/\S+|mailto:\S+|tel:\S+)$/i.test(v)) return v;
  if (/^[^\s/@:]+@[^\s/@:]+\.[^\s/@:]+$/.test(v)) return `mailto:${v}`;
  if (/^[^\s:]+\.[^\s:]+(:\d+)?(\/\S*)?$/.test(v)) return `https://${v}`;
  return null;
}

const openableHref = (href: string) => /^(https?:|mailto:|tel:)/i.test(href);

function LinkDialog({ editor, open, onOpenChange }: { editor: Editor; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [text, setText] = React.useState("");
  const [href, setHref] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [multiline, setMultiline] = React.useState(false);
  const [seenOpen, setSeenOpen] = React.useState(false);
  if (open !== seenOpen) {
    setSeenOpen(open);
    if (open) {
      if (editor.isActive("link")) editor.commands.extendMarkRange("link");
      const { from, to } = editor.state.selection;
      const selected = editor.state.doc.textBetween(from, to, "\n");
      setText(selected);
      setMultiline(selected.includes("\n"));
      setHref((editor.getAttributes("link").href as string | undefined) ?? "");
      setError(null);
    }
  }
  function apply(e: React.FormEvent) {
    e.preventDefault();
    e.stopPropagation(); // the dialog may sit inside another form, and React events bubble through portals
    const target = toHref(href);
    if (!target) {
      setError("Enter a web address, an email address or a {{variable}}, such as trace-studio.tech");
      return;
    }
    if (multiline) {
      editor.chain().focus().setLink({ href: target }).run();
    } else {
      const { from, to } = editor.state.selection;
      editor.chain().focus().insertContentAt({ from, to }, textNode(text.trim() || target, target)).run();
    }
    onOpenChange(false);
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{editor.isActive("link") ? "Edit link" : "Insert link"}</DialogTitle>
        </DialogHeader>
        <form className="grid gap-3" onSubmit={apply}>
          {!multiline && (
            <Field label="Text to display" htmlFor="link-text">
              <Input id="link-text" value={text} onChange={(e) => setText(e.target.value)} placeholder="See our work" />
            </Field>
          )}
          <Field label="Link to" htmlFor="link-href">
            <Input id="link-href" autoFocus value={href} placeholder="https://trace-studio.tech" aria-invalid={!!error}
              onChange={(e) => { setHref(e.target.value); setError(null); }} />
          </Field>
          {error && <p className="text-xs text-destructive" role="alert">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit">OK</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export type EmailEditorHandle = { insertText: (text: string) => void };

/** A compose box like Gmail's: links sit on the words, and Enter starts a new line. Reads and writes the stored text. */
export function EmailEditor({ id, value, onChange, readOnly, signatureButton, editorRef }: {
  id?: string;
  value: string;
  onChange: (text: string) => void;
  readOnly?: boolean;
  signatureButton?: boolean;
  editorRef?: React.Ref<EmailEditorHandle>;
}) {
  const [linkOpen, setLinkOpen] = React.useState(false);
  const emitted = React.useRef(value);
  const latestOnChange = React.useRef(onChange);
  React.useEffect(() => { latestOnChange.current = onChange; });
  const editor = useEditor({
    immediatelyRender: false, // the server has no document to draw this on
    editable: !readOnly,
    extensions: [Document, Paragraph, Text, HardBreak, UndoRedo, Link.configure({
      openOnClick: false,
      autolink: true,
      linkOnPaste: true,
      protocols: ["mailto", "tel"],
      isAllowedUri: (url) => !!url && (openableHref(url) || url.startsWith("{{")),
    })],
    content: textToDoc(value),
    editorProps: {
      attributes: {
        ...(id ? { id } : {}),
        role: "textbox",
        "aria-multiline": "true",
        class: "min-h-40 px-2.5 py-2 text-sm leading-relaxed outline-none [&_a]:cursor-text [&_a]:text-primary [&_a]:underline [&_p]:m-0 [&_p]:min-h-[1.625em]",
      },
      // keeps blank lines, which the default paste drops, and turns [label](url) and addresses into links
      clipboardTextParser: (text, _context, _plain, view) => Slice.maxOpen(Fragment.from(view.state.schema.nodeFromJSON(textToDoc(text)).content)),
    },
    onUpdate: ({ editor: e }) => {
      const next = docToText(e.getJSON());
      if (next === emitted.current) return; // link detection on load, for one, changes nothing the sender would see
      emitted.current = next;
      latestOnChange.current(next);
    },
  });
  const link = useEditorState({
    editor,
    selector: ({ editor: e }) => (e?.isActive("link") ? ((e.getAttributes("link").href as string | undefined) ?? "") : null),
  });

  React.useEffect(() => {
    if (editor && value !== emitted.current) { // discarded, or replaced from a template
      emitted.current = value;
      editor.commands.setContent(textToDoc(value), { emitUpdate: false });
    }
  }, [editor, value]);
  React.useEffect(() => { editor?.setEditable(!readOnly); }, [editor, readOnly]);
  React.useImperativeHandle(editorRef, () => ({
    insertText: (text) => { editor?.chain().focus().insertContent(textNode(text)).run(); },
  }), [editor]);

  function removeLink() {
    editor?.chain().focus().extendMarkRange("link").unsetLink().run();
  }
  function addSignature() {
    if (!editor) return;
    if (SIGNATURE.test(docToText(editor.getJSON()))) return void editor.commands.focus();
    const last = docToText(editor.getJSON()).trimEnd().split("\n").pop() ?? "";
    // "Best," keeps the signature under it; after a sentence it gets a blank line of its own
    const lines = last && !last.endsWith(",") ? ["", "{{signature}}"] : ["{{signature}}"];
    const paragraphs = lines.map((line) => ({ type: "paragraph", content: lineToNodes(line) }));
    editor.chain().focus().insertContentAt(editor.state.doc.content.size, paragraphs).run();
  }

  return (
    <div className="overflow-hidden rounded-lg border border-input bg-transparent focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50"
      onKeyDown={(e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && !readOnly) {
          e.preventDefault();
          e.stopPropagation(); // not the app's command palette
          setLinkOpen(true);
        }
      }}>
      <EditorContent editor={editor} className={readOnly ? "opacity-60" : undefined} />
      {!readOnly && editor && (
        <div className="flex flex-wrap items-center gap-1 border-t bg-muted/30 px-1.5 py-1">
          <Button type="button" variant="ghost" size="icon-sm" aria-label="Insert link" title="Insert link (Ctrl+K)" onClick={() => setLinkOpen(true)}>
            <Link2 />
          </Button>
          {signatureButton && (
            <Button type="button" variant="ghost" size="sm" title="Add the sending mailbox’s signature" onClick={addSignature}>
              <PenLine /> Signature
            </Button>
          )}
          {link !== null && (
            <div className="ml-auto flex min-w-0 items-center gap-0.5 text-xs" role="group" aria-label="Link under the cursor">
              {openableHref(link) ? (
                <a href={link} target="_blank" rel="noreferrer" className="flex min-w-0 items-center gap-1 px-1 text-primary underline">
                  <ExternalLink className="size-3 shrink-0" /><span className="max-w-48 truncate">{link}</span>
                </a>
              ) : (
                <span className="max-w-48 truncate px-1 font-mono">{link}</span>
              )}
              <Button type="button" variant="ghost" size="icon-xs" aria-label="Change link" title="Change" onClick={() => setLinkOpen(true)}><Pencil /></Button>
              <Button type="button" variant="ghost" size="icon-xs" aria-label="Remove link" title="Remove" onClick={removeLink}><Unlink /></Button>
            </div>
          )}
        </div>
      )}
      {editor && <LinkDialog editor={editor} open={linkOpen} onOpenChange={setLinkOpen} />}
    </div>
  );
}
