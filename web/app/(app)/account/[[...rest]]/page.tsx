// Account — Clerk handles most of this via <UserProfile/>. Clerk needs
// catch-all routing so its sub-pages (security, connected accounts, etc.)
// can resolve under /account/*, hence the [[...rest]] segment.
import { UserProfile } from "@clerk/nextjs";

export default function AccountPage() {
  return (
    <div className="flex justify-center">
      <UserProfile path="/account" />
    </div>
  );
}
