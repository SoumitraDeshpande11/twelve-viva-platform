"use client";

import { FormEvent, useState } from "react";
import { Key as KeyRound, SignIn as LogIn, UserPlus } from "@phosphor-icons/react";
import { api } from "../lib/api";
import { Card, SectionTitle } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Field, Input } from "../components/ui/Field";
import { Banner } from "../components/ui/Banner";

type AuthResponse = { csrf_token: string };

export function AuthPanel({ onReady }: { onReady: () => void }) {
  const [mode, setMode] = useState<"login" | "bootstrap">("login");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    setBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      if (mode === "login") {
        await api<AuthResponse>("/api/auth/login", {
          method: "POST",
          body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
        });
      } else {
        await api<AuthResponse>("/api/auth/bootstrap", {
          method: "POST",
          body: JSON.stringify({
            email: form.get("email"),
            name: form.get("name"),
            password: form.get("password"),
            bootstrap_token: form.get("bootstrap_token") || undefined,
          }),
        });
      }
      onReady();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Authentication failed.");
    } finally {
      setBusy(false);
    }
  }

  const isLogin = mode === "login";

  return (
    <div className="reveal mx-auto flex max-w-md flex-col items-center py-10">
      <span className="mb-5 flex h-12 w-12 items-center justify-center rounded-full border border-accent/30 bg-accent-soft text-accent">
        <KeyRound size={20} />
      </span>
      <Card className="w-full p-6">
        <SectionTitle
          marker={isLogin ? "№ —" : "№ 00"}
          title={isLogin ? "Staff Login" : "First Staff Account"}
          hint={isLogin ? "Admin and review access for professors and invigilators." : "Bootstrap the first account; the endpoint disables once any user exists."}
        />
        <form onSubmit={submit} className="mt-6">
          {!isLogin && (
            <Field label="Name" htmlFor="name">
              <Input id="name" name="name" required placeholder="Prof. Asha Rao" />
            </Field>
          )}
          <Field label="Email" htmlFor="email">
            <Input id="email" name="email" type="email" required placeholder="staff@example.edu" />
          </Field>
          <Field label="Password" htmlFor="password" hint="Minimum 10 characters.">
            <Input id="password" name="password" type="password" required minLength={10} />
          </Field>
          {!isLogin && (
            <Field label="Setup token" htmlFor="bootstrap_token" hint="From TWELVE_BOOTSTRAP_TOKEN — required outside local dev.">
              <Input id="bootstrap_token" name="bootstrap_token" type="password" placeholder="Server-side setup secret" />
            </Field>
          )}
          <Button variant="primary" type="submit" disabled={busy} className="w-full">
            {isLogin ? <LogIn size={16} /> : <UserPlus size={16} />}
            {busy ? "Working…" : isLogin ? "Log in" : "Create admin"}
          </Button>
        </form>
        {message && (
          <div className="mt-4">
            <Banner tone="danger">{message}</Banner>
          </div>
        )}
        <button
          type="button"
          onClick={() => {
            setMode(isLogin ? "bootstrap" : "login");
            setMessage("");
          }}
          className="mt-5 w-full text-center text-[0.78rem] text-muted transition-colors hover:text-accent"
        >
          {isLogin ? "First time here? Set up the initial staff account →" : "← Back to login"}
        </button>
      </Card>
    </div>
  );
}
