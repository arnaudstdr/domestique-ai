import { useEffect, useState } from "react";
import { Brain, Check, Pencil, Pin, PinOff, Plus, Trash2, X } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { CoachMemoryCategory, CoachMemoryFact } from "../api/types";
import { useToast } from "../hooks/useToast";

const CATEGORY_LABELS: Record<CoachMemoryCategory, string> = {
  preference: "Préférences",
  constraint: "Contraintes",
  goal: "Objectifs",
  agreement: "Accords",
  personal: "Perso",
};

const CATEGORY_ORDER: CoachMemoryCategory[] = [
  "constraint",
  "goal",
  "preference",
  "agreement",
  "personal",
];

export default function MemoryPanel() {
  const [facts, setFacts] = useState<CoachMemoryFact[]>([]);
  const [loading, setLoading] = useState(true);
  const [category, setCategory] = useState<CoachMemoryCategory>("personal");
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editContent, setEditContent] = useState("");
  const { push } = useToast();

  function load() {
    api.coach.memory
      .list()
      .then(setFacts)
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  async function add() {
    const text = content.trim();
    if (!text || saving) return;
    setSaving(true);
    try {
      const fact = await api.coach.memory.create({ category, content: text });
      setFacts((prev) => [fact, ...prev]);
      setContent("");
      push("Fait mémorisé.", "success");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Mémoire : ${msg}`, "error");
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: number) {
    try {
      await api.coach.memory.remove(id);
      setFacts((prev) => prev.filter((f) => f.id !== id));
    } catch {
      push("Impossible de supprimer ce fait.", "error");
    }
  }

  async function togglePin(fact: CoachMemoryFact) {
    try {
      const updated = await api.coach.memory.update(fact.id, { pinned: !fact.pinned });
      setFacts((prev) => prev.map((f) => (f.id === fact.id ? updated : f)));
    } catch {
      push("Impossible de modifier ce fait.", "error");
    }
  }

  async function saveEdit(id: number) {
    const text = editContent.trim();
    if (!text) return;
    try {
      const updated = await api.coach.memory.update(id, { content: text });
      setFacts((prev) => prev.map((f) => (f.id === id ? updated : f)));
      setEditingId(null);
    } catch {
      push("Impossible de modifier ce fait.", "error");
    }
  }

  const grouped = CATEGORY_ORDER.map((cat) => ({
    cat,
    items: facts.filter((f) => f.category === cat),
  })).filter((g) => g.items.length > 0);

  return (
    <section className="card space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-medium text-gray-200">
        <Brain className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
        Mémoire du coach
      </h3>
      <p className="text-xs text-muted">
        Ce que le coach retient entre les sessions : préférences, contraintes,
        objectifs, accords. Il alimente automatiquement ses réponses et
        mémorise les faits importants au fil des échanges. Tu peux corriger ou
        supprimer ici.
      </p>

      <div className="grid grid-cols-[minmax(0,140px)_1fr] gap-2">
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value as CoachMemoryCategory)}
          className="input"
          aria-label="Catégorie"
        >
          {CATEGORY_ORDER.map((cat) => (
            <option key={cat} value={cat}>
              {CATEGORY_LABELS[cat]}
            </option>
          ))}
        </select>
        <input
          type="text"
          value={content}
          placeholder="Ex. « Genou droit sensible au froid »"
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") add();
          }}
          className="input"
        />
      </div>
      <button
        type="button"
        onClick={add}
        disabled={saving || !content.trim()}
        className="btn-primary w-full disabled:opacity-50"
      >
        <span className="inline-flex items-center justify-center gap-2">
          <Plus size={16} />
          {saving ? "Enregistrement…" : "Mémoriser"}
        </span>
      </button>

      {loading ? (
        <p className="text-xs text-muted">Chargement…</p>
      ) : facts.length === 0 ? (
        <p className="text-xs text-muted">
          Aucun fait mémorisé pour l'instant.
        </p>
      ) : (
        <div className="space-y-3">
          {grouped.map(({ cat, items }) => (
            <div key={cat} className="space-y-1.5">
              <p className="label-eyebrow">{CATEGORY_LABELS[cat]}</p>
              {items.map((fact) => (
                <div
                  key={fact.id}
                  className="flex items-start gap-2 rounded-lg border border-white/[0.07] bg-card/60 px-3 py-2"
                >
                  {editingId === fact.id ? (
                    <>
                      <input
                        type="text"
                        value={editContent}
                        onChange={(e) => setEditContent(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") saveEdit(fact.id);
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        className="input flex-1"
                        autoFocus
                      />
                      <button
                        onClick={() => saveEdit(fact.id)}
                        className="btn-ghost !px-2 !py-1"
                        aria-label="Valider"
                      >
                        <Check size={15} />
                      </button>
                      <button
                        onClick={() => setEditingId(null)}
                        className="btn-ghost !px-2 !py-1"
                        aria-label="Annuler"
                      >
                        <X size={15} />
                      </button>
                    </>
                  ) : (
                    <>
                      <span className="flex-1 text-sm text-gray-200">{fact.content}</span>
                      <button
                        onClick={() => togglePin(fact)}
                        className="btn-ghost !px-2 !py-1"
                        title={fact.pinned ? "Ne plus épingler" : "Épingler (toujours transmis)"}
                        aria-label={fact.pinned ? "Ne plus épingler" : "Épingler"}
                      >
                        {fact.pinned ? (
                          <Pin size={15} className="text-accent" />
                        ) : (
                          <PinOff size={15} />
                        )}
                      </button>
                      <button
                        onClick={() => {
                          setEditingId(fact.id);
                          setEditContent(fact.content);
                        }}
                        className="btn-ghost !px-2 !py-1"
                        aria-label="Modifier"
                      >
                        <Pencil size={15} />
                      </button>
                      <button
                        onClick={() => remove(fact.id)}
                        className="btn-ghost !px-2 !py-1"
                        aria-label="Supprimer"
                      >
                        <Trash2 size={15} />
                      </button>
                    </>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
