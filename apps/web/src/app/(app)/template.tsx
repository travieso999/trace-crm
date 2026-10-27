// A template (unlike a layout) is re-mounted on every navigation, so each page plays a short enter animation.
export default function PageTransition({ children }: { children: React.ReactNode }) {
  return <div className="animate-page-in">{children}</div>;
}
