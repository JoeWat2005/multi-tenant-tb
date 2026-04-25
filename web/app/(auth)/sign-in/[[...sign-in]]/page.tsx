// Clerk-hosted sign-in form. Catch-all so Clerk can mount whatever sub-route
// it needs (SSO callbacks, verifications, etc.).
import { SignIn } from "@clerk/nextjs";

export default function Page() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <SignIn />
    </div>
  );
}
