import { useState } from "react";
import { useLocation } from "react-router-dom";
import { Send } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { FeedbackCategory } from "../api/types";
import { useToast } from "../hooks/useToast";

const CATEGORIES: { value: FeedbackCategory; label: string }[] = [
  { value: "bug", label: "Bug / dysfonctionnement" },
  { value: "idea", label: "Idée d'amélioration" },
  { value: "remark", label: "Remarque générale" },
  { value: "other", label: "Autre" },
];

export default function Feedback() {
  const location = useLocation();
  const { push } = useToast();
  const [category, setCategory] = useState<FeedbackCategory>("idea");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);

  const canSubmit = message.trim().length > 0 && !saving;

  async function submit() {
    if (!canSubmit) return;
    setSaving(true);
    try {
      await api.feedback.create({
        category,
        message: message.trim(),
        page: location.pathname,
        app_version: __APP_VERSION__,
      });
      setMessage("");
      push("Merci pour ton retour !", "success");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Envoi du retour : ${msg}`, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="stagger space-y-4">
      <div>
        <h2 className="font-display text-2xl font-extrabold tracking-tight text-fg">
          Ton retour
        </h2>
        <p className="mt-1 text-sm text-muted">
          Un bug, une idée, une remarque ? Dis-nous tout, ça nous aide beaucoup.
        </p>
      </div>

      <section className="card space-y-3">
        <label className="block">
          <span className="text-xs text-muted">Catégorie</span>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value as FeedbackCategory)}
            className="input mt-1"
          >
            {CATEGORIES.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-muted">Message</span>
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={6}
            maxLength={4000}
            placeholder="Décris ce que tu as remarqué…"
            className="input mt-1 resize-none"
          />
        </label>
        <button
          onClick={submit}
          disabled={!canSubmit}
          className="btn-primary w-full disabled:opacity-50"
        >
          {saving ? (
            "Envoi…"
          ) : (
            <span className="inline-flex items-center justify-center gap-2">
              <Send className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              Envoyer mon retour
            </span>
          )}
        </button>
      </section>
    </div>
  );
}
