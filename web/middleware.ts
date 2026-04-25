// Clerk middleware — gates every route except /sign-in, /sign-up.
// See https://clerk.com/docs/references/nextjs/clerk-middleware
import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

const isPublic = createRouteMatcher(["/sign-in(.*)", "/sign-up(.*)"]);

export default clerkMiddleware(async (auth, req) => {
  if (isPublic(req)) return;

  // In Clerk 6.x, `auth` is a function that returns a Promise.
  const { userId, redirectToSignIn } = await auth();
  if (!userId) return redirectToSignIn();
});

export const config = {
  // Skip Next internals + static files, run on everything else incl. API routes.
  matcher: ["/((?!_next|.*\\..*).*)", "/", "/(api|trpc)(.*)"],
};
