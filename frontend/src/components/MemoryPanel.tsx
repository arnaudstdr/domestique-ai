import { useEffect, useMemo, useRef, useState } from "react";
import type { ComponentType, SVGProps } from "react";
import {
  Brain,
  Check,
  ChevronDown,
  Handshake,
  Heart,
  Info,
  MoreVertical,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Search,
  ShieldAlert,
  Target,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import type { CoachMemoryCategory, CoachMemoryFact } from "../api/types";
import { useToast } from "../hooks/useToast";

type IconType = ComponentType<SVGProps<SVGSVGElement> & { size?: number | string }>;

interface CategoryStyle {
  label: string;
  Icon: IconType;
  accent: string;
  border: string;
}

const CATEGORY_CONFIG: Record<CoachMemoryCategory, CategoryStyle> = {
  constraint: {
    label: "Contraintes",
    Icon: ShieldAlert,
    accent: "text-amber-400",
    border: "border-amber-400/20",
  },
  goal: {
    label: "Objectifs",
    Icon: Target,
    accent: "text-accent",
    border: "border-accent/20",
  },
  preference: {
    label: "Préférences",
    Icon: Heart,
    accent: "text-sky-400",
    border: "border-sky-400/20",
  },
  agreement: {
    label: "Accords",
    Icon: Handshake,
    accent: "text-violet-400",
    border: "border-violet-400/20",
  },
  personal: {
    label: "Perso",
    Icon: UserRound,
    accent: "text-rose-400",
    border: "border-rose-400/20",
  },
};

const CATEGORY_ORDER: CoachMemoryCategory[] = [
  "constraint",
  "goal",
  "preference",
  "agreement",
  "personal",
];

const OPEN_KEY = "memory-open-cats";

const DEFAULT_OPEN: Record<CoachMemoryCategory, boolean> = {
  constraint: true,
  goal: true,
  preference: true,
  agreement: true,
  personal: true,
};

function loadOpenCats(): Record<CoachMemoryCategory, boolean> {
  try {
    const raw = localStorage.getItem(OPEN_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<Record<CoachMemoryCategory, boolean>>;
      return { ...DEFAULT_OPEN, ...parsed };
    }
  } catch {
    // stockage indisponible / contenu corrompu → valeurs par défaut
  }
  return DEFAULT_OPEN;
}

function normalize(value: string): string {
  return value
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");
}

export default function MemoryPanel() {
  const [facts, setFacts] = useState<CoachMemoryFact[]>([]);
  const [loading, setLoading] = useState(true);
  const [category, setCategory] = useState<CoachMemoryCategory>("personal");
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editContent, setEditContent] = useState("");
  const [query, setQuery] = useState("");
  const [menuId, setMenuId] = useState<number | null>(null);
  const [showHelp, setShowHelp] = useState(false);
  const [openCats, setOpenCats] = useState<Record<CoachMemoryCategory, boolean>>(loadOpenCats);
  const { push } = useToast();

  function load() {
    api.coach.memory
      .list()
      .then(setFacts)
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  function setCatOpen(cat: CoachMemoryCategory, open: boolean) {
    setOpenCats((prev) => {
      if (prev[cat] === open) return prev;
      const next = { ...prev, [cat]: open };
      try {
        localStorage.setItem(OPEN_KEY, JSON.stringify(next));
      } catch {
        // stockage indisponible : l'état reste valable pour la session
      }
      return next;
    });
  }

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
    setMenuId(null);
    try {
      await api.coach.memory.remove(id);
      setFacts((prev) => prev.filter((f) => f.id !== id));
    } catch {
      push("Impossible de supprimer ce fait.", "error");
    }
  }

  async function togglePin(fact: CoachMemoryFact) {
    setMenuId(null);
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

  const filtered = useMemo(() => {
    const q = normalize(query.trim());
    if (!q) return facts;
    return facts.filter(
      (f) =>
        normalize(f.content).includes(q) || normalize(CATEGORY_CONFIG[f.category].label).includes(q),
    );
  }, [facts, query]);

  const pinned = filtered.filter((f) => f.pinned);
  const grouped = CATEGORY_ORDER.map((cat) => ({
    cat,
    items: filtered.filter((f) => !f.pinned && f.category === cat),
  })).filter((g) => g.items.length > 0);

  const visibleCount = pinned.length + grouped.reduce((sum, g) => sum + g.items.length, 0);
  const searching = query.trim().length > 0;

  const rowProps = {
    editingId,
    editContent,
    setEditContent,
    setEditingId,
    saveEdit,
    menuId,
    setMenuId,
    togglePin,
    remove,
  };

  return (
    <section className="card space-y-4">
      <div className="flex items-start gap-2">
        <h3 className="flex items-center gap-2 text-sm font-medium text-gray-200">
          <Brain className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
          Mémoire du coach
        </h3>
        {facts.length > 0 && (
          <span className="pill bg-white/[0.06] text-muted">{facts.length}</span>
        )}
        <button
          type="button"
          onClick={() => setShowHelp((v) => !v)}
          className="btn-ghost ml-auto !px-2 !py-1"
          aria-label="À propos de la mémoire"
          aria-expanded={showHelp}
        >
          <Info size={15} />
        </button>
      </div>

      {showHelp && (
        <p className="text-xs leading-relaxed text-muted">
          Ce que le coach retient entre les sessions : préférences, contraintes, objectifs,
          accords. Il alimente automatiquement ses réponses et mémorise les faits importants au fil
          des échanges. Tu peux corriger ou supprimer ici.
        </p>
      )}

      <div className="grid grid-cols-[minmax(0,140px)_1fr_auto] gap-2">
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value as CoachMemoryCategory)}
          className="input"
          aria-label="Catégorie"
        >
          {CATEGORY_ORDER.map((cat) => (
            <option key={cat} value={cat}>
              {CATEGORY_CONFIG[cat].label}
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
          aria-label="Nouveau fait à mémoriser"
        />
        <button
          type="button"
          onClick={add}
          disabled={saving || !content.trim()}
          className="btn-primary !px-3"
          aria-label="Mémoriser"
          title="Mémoriser"
        >
          <Plus size={18} />
        </button>
      </div>

      {facts.length >= 4 && (
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted"
            strokeWidth={1.75}
            aria-hidden="true"
          />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Rechercher un fait…"
            className="input pl-9"
            aria-label="Rechercher dans la mémoire"
          />
        </div>
      )}

      {loading ? (
        <p className="text-xs text-muted">Chargement…</p>
      ) : facts.length === 0 ? (
        <p className="text-xs text-muted">Aucun fait mémorisé pour l'instant.</p>
      ) : visibleCount === 0 ? (
        <p className="text-xs text-muted">
          Aucun fait ne correspond à « <span className="text-gray-200">{query.trim()}</span> ».
        </p>
      ) : (
        <div className="space-y-3">
          {pinned.length > 0 && (
            <div className="card border-accent/25">
              <div className="mb-1 flex items-center gap-2">
                <Pin className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
                <span className="text-sm font-medium text-gray-200">Épinglés</span>
                <span className="pill bg-white/[0.06] text-muted">{pinned.length}</span>
                <span className="ml-auto text-[11px] text-muted">toujours transmis</span>
              </div>
              <div>
                {pinned.map((fact) => (
                  <FactRow key={fact.id} fact={fact} {...rowProps} />
                ))}
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {grouped.map(({ cat, items }) => (
              <CategoryCard
                key={cat}
                cat={cat}
                items={items}
                open={openCats[cat]}
                onToggle={(open) => setCatOpen(cat, open)}
                rowProps={rowProps}
              />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

interface RowProps {
  editingId: number | null;
  editContent: string;
  setEditContent: (v: string) => void;
  setEditingId: (v: number | null) => void;
  saveEdit: (id: number) => void;
  menuId: number | null;
  setMenuId: (v: number | null) => void;
  togglePin: (fact: CoachMemoryFact) => void;
  remove: (id: number) => void;
}

function CategoryCard({
  cat,
  items,
  open,
  onToggle,
  rowProps,
}: {
  cat: CoachMemoryCategory;
  items: CoachMemoryFact[];
  open: boolean;
  onToggle: (open: boolean) => void;
  rowProps: RowProps;
}) {
  const { label, Icon, accent, border } = CATEGORY_CONFIG[cat];
  return (
    <details
      open={open}
      onToggle={(e) => onToggle((e.target as HTMLDetailsElement).open)}
      className={`card border ${border}`}
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
        <Icon className={`h-4 w-4 ${accent}`} strokeWidth={1.75} aria-hidden="true" />
        <span className="text-sm font-medium text-gray-200">{label}</span>
        <span className="pill bg-white/[0.06] text-muted">{items.length}</span>
        <ChevronDown
          className={`ml-auto h-4 w-4 text-muted transition-transform duration-200 motion-reduce:transition-none ${
            open ? "rotate-180" : ""
          }`}
          strokeWidth={1.75}
          aria-hidden="true"
        />
      </summary>
      <div className="mt-1">
        {items.map((fact) => (
          <FactRow key={fact.id} fact={fact} {...rowProps} />
        ))}
      </div>
    </details>
  );
}

function FactRow({ fact, ...rp }: { fact: CoachMemoryFact } & RowProps) {
  if (rp.editingId === fact.id) {
    return (
      <div className="flex items-center gap-2 border-b border-white/5 py-2 last:border-0">
        <input
          type="text"
          value={rp.editContent}
          onChange={(e) => rp.setEditContent(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") rp.saveEdit(fact.id);
            if (e.key === "Escape") rp.setEditingId(null);
          }}
          className="input flex-1"
          autoFocus
          aria-label="Modifier le fait"
        />
        <button
          onClick={() => rp.saveEdit(fact.id)}
          className="btn-ghost !px-2 !py-1"
          aria-label="Valider"
        >
          <Check size={15} />
        </button>
        <button
          onClick={() => rp.setEditingId(null)}
          className="btn-ghost !px-2 !py-1"
          aria-label="Annuler"
        >
          <X size={15} />
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-2 border-b border-white/5 py-2 last:border-0">
      {fact.pinned && (
        <Pin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" strokeWidth={1.75} aria-hidden="true" />
      )}
      <span className="flex-1 text-sm leading-relaxed text-gray-200">{fact.content}</span>
      <FactMenu
        fact={fact}
        open={rp.menuId === fact.id}
        setMenuId={rp.setMenuId}
        onEdit={() => {
          rp.setMenuId(null);
          rp.setEditingId(fact.id);
          rp.setEditContent(fact.content);
        }}
        onTogglePin={() => rp.togglePin(fact)}
        onRemove={() => rp.remove(fact.id)}
      />
    </div>
  );
}

function FactMenu({
  fact,
  open,
  setMenuId,
  onEdit,
  onTogglePin,
  onRemove,
}: {
  fact: CoachMemoryFact;
  open: boolean;
  setMenuId: (v: number | null) => void;
  onEdit: () => void;
  onTogglePin: () => void;
  onRemove: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDown(e: PointerEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setMenuId(null);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setMenuId(null);
    }
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, setMenuId]);

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setMenuId(open ? null : fact.id)}
        className="btn-ghost !px-2 !py-1"
        aria-label="Actions sur le fait"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <MoreVertical size={15} />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-20 mt-1 w-48 overflow-hidden rounded-xl border border-white/10 bg-surface/95 py-1 shadow-card backdrop-blur"
        >
          <MenuItem
            icon={fact.pinned ? PinOff : Pin}
            label={fact.pinned ? "Ne plus épingler" : "Épingler"}
            onClick={onTogglePin}
          />
          <MenuItem icon={Pencil} label="Modifier" onClick={onEdit} />
          <MenuItem icon={Trash2} label="Supprimer" onClick={onRemove} danger />
        </div>
      )}
    </div>
  );
}

function MenuItem({
  icon: Icon,
  label,
  onClick,
  danger = false,
}: {
  icon: IconType;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-white/[0.06] ${
        danger ? "text-rose-400" : "text-gray-200"
      }`}
    >
      <Icon size={15} strokeWidth={1.75} aria-hidden="true" />
      {label}
    </button>
  );
}
