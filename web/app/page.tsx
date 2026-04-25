// Root — redirect authenticated users to /overview, unauth to /sign-in.
import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

export default async function RootPage() {
  const { userId } = await auth();
  redirect(userId ? "/overview" : "/sign-in");
}
