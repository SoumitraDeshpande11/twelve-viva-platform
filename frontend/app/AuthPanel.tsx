"use client";

import { FormEvent, useState } from "react";
import { LogIn, UserPlus } from "lucide-react";
import { api } from "../lib/api";

type AuthResponse = {
  csrf_token: string;
};

export function AuthPanel({ onReady }: { onReady: () => void }) {
  const [mode, setMode] = useState<"login" | "bootstrap">("login");
  const [message, setMessage] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    const form = new FormData(event.currentTarget);
    try {
      if (mode === "login") {
        await api<AuthResponse>("/api/auth/login", {
          method: "POST",
          body: JSON.stringify({ email: form.get("email"), password: form.get("password") })
        });
      } else {
        await api<AuthResponse>("/api/auth/bootstrap", {
          method: "POST",
          body: JSON.stringify({ email: form.get("email"), name: form.get("name"), password: form.get("password") })
        });
      }
      onReady();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Authentication failed.");
    }
  }

  return (
    <main className="shell">
      <section className="panel auth-panel">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h1 className="section-title" style={{ margin: 0 }}>{mode === "login" ? "Staff Login" : "First Staff Account"}</h1>
          <button type="button" className="secondary" onClick={() => setMode(mode === "login" ? "bootstrap" : "login")}>
            {mode === "login" ? <UserPlus size={17} /> : <LogIn size={17} />}
            {mode === "login" ? "First setup" : "Login"}
          </button>
        </div>
        <form onSubmit={submit} style={{ marginTop: 16 }}>
          {mode === "bootstrap" && (
            <div className="field">
              <label htmlFor="name">Name</label>
              <input id="name" name="name" required placeholder="Professor or admin name" />
            </div>
          )}
          <div className="field">
            <label htmlFor="email">Email</label>
            <input id="email" name="email" required type="email" />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input id="password" name="password" required type="password" minLength={10} />
          </div>
          <button className="primary" type="submit">
            {mode === "login" ? <LogIn size={17} /> : <UserPlus size={17} />}
            {mode === "login" ? "Log in" : "Create admin"}
          </button>
        </form>
        {message && <p className="small muted">{message}</p>}
      </section>
    </main>
  );
}
