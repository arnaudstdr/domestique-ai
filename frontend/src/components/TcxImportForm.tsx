import { useRef, useState } from "react";
import { Upload, X } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { TcxImportResponse } from "../api/types";
import { useToast } from "../hooks/useToast";

interface Props {
  onImported: () => void;
  onClose: () => void;
}

export default function TcxImportForm({ onImported, onClose }: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<TcxImportResponse | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const { push } = useToast();

  async function submit() {
    if (files.length === 0 || saving) return;
    setSaving(true);
    setResult(null);
    try {
      const res = await api.activities.importTcx(files);
      setResult(res);
      if (res.imported > 0) {
        push(`${res.imported} activité(s) importée(s).`, "success");
        onImported();
      } else {
        push("Aucune activité importée.", "info");
      }
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      push(`Erreur : ${msg}`, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="font-display text-sm font-bold text-gray-50">
          Importer des fichiers TCX
        </h3>
        <button onClick={onClose} className="btn-ghost !px-2 !py-1" aria-label="Fermer">
          <X size={16} />
        </button>
      </div>

      <label className="block">
        <span className="text-xs text-muted">Fichiers (.tcx, plusieurs possibles)</span>
        <input
          ref={inputRef}
          type="file"
          accept=".tcx,application/xml,text/xml"
          multiple
          onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          className="input mt-1 file:mr-3 file:rounded file:border-0 file:bg-accent/15 file:px-2 file:py-1 file:text-accent"
        />
      </label>

      {files.length > 0 && (
        <p className="text-xs text-muted">
          {files.length} fichier(s) sélectionné(s).
        </p>
      )}

      <button
        onClick={submit}
        disabled={files.length === 0 || saving}
        className="btn-primary w-full disabled:opacity-50"
      >
        <span className="inline-flex items-center justify-center gap-2">
          <Upload size={16} />
          {saving ? "Import en cours…" : "Importer"}
        </span>
      </button>

      {result && (
        <ul className="space-y-1 text-xs">
          {result.results.map((r, i) => (
            <li key={`${r.filename}-${i}`} className="flex items-start gap-2">
              <span
                className={
                  r.status === "imported"
                    ? "text-accent"
                    : r.status === "skipped"
                      ? "text-muted"
                      : "text-tsb_neg"
                }
              >
                ●
              </span>
              <span className="min-w-0 flex-1">
                <span className="text-gray-200">{r.filename}</span>
                {" — "}
                {r.status === "imported"
                  ? `${r.activities.length} activité(s) importée(s)`
                  : r.status === "skipped"
                    ? r.reason || "ignoré"
                    : r.reason || "erreur"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
