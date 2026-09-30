import { Info } from "lucide-react";

const DEFAULT_TEXT =
  "Le coach est une IA : il peut faire des erreurs. Ses conseils ne remplacent pas l'avis d'un médecin ou d'un professionnel.";

interface Props {
  text?: string;
  className?: string;
}

export default function LlmDisclaimer({ text = DEFAULT_TEXT, className = "" }: Props) {
  return (
    <p
      className={`flex items-center justify-center gap-1.5 text-center text-[11px] leading-tight text-muted ${className}`}
    >
      <Info className="h-3 w-3 shrink-0" strokeWidth={2} aria-hidden="true" />
      {text}
    </p>
  );
}
