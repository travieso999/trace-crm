import { NextResponse, type NextRequest } from "next/server";

// Optimistic check only: real authorization happens in the API on every request.
export function proxy(request: NextRequest) {
  const hasSession = request.cookies.has("ts_session");
  const { pathname } = request.nextUrl;
  if (!hasSession && pathname !== "/login") {
    const url = new URL("/login", request.url);
    if (pathname !== "/") url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  // Skip API/unsubscribe proxying, Next internals and static files
  matcher: ["/((?!api/|u/|_next/|favicon.ico|.*\\.(?:svg|png|jpg|ico|webp)$).*)"],
};
