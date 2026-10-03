interface ConsentCheckboxesProps {
  acceptsTerms: boolean;
  acceptsHealthData: boolean;
  onChangeTerms: (value: boolean) => void;
  onChangeHealthData: (value: boolean) => void;
  disabled?: boolean;
}

/**
 * Cases de consentement (CGU/confidentialité + données de santé), volontairement
 * décochées par défaut. Les liens s'ouvrent dans un nouvel onglet pour ne pas
 * perdre le formulaire en cours.
 */
export default function ConsentCheckboxes({
  acceptsTerms,
  acceptsHealthData,
  onChangeTerms,
  onChangeHealthData,
  disabled,
}: ConsentCheckboxesProps) {
  return (
    <div className="space-y-3">
      <label className="flex items-start gap-2.5 text-xs text-fg-soft">
        <input
          type="checkbox"
          checked={acceptsTerms}
          disabled={disabled}
          onChange={(event) => onChangeTerms(event.target.checked)}
          className="mt-0.5 h-4 w-4 shrink-0 accent-[rgb(var(--accent))]"
        />
        <span>
          J'ai au moins 15 ans révolus et, si je suis mineur·e, je dispose de
          l'autorisation de mon représentant légal. J'accepte les{" "}
          <a
            href="/cgu"
            target="_blank"
            rel="noreferrer"
            className="text-accent underline decoration-accent/40 hover:decoration-accent"
          >
            conditions générales d'utilisation
          </a>{" "}
          et la{" "}
          <a
            href="/confidentialite"
            target="_blank"
            rel="noreferrer"
            className="text-accent underline decoration-accent/40 hover:decoration-accent"
          >
            politique de confidentialité
          </a>
          .
        </span>
      </label>
      <label className="flex items-start gap-2.5 text-xs text-fg-soft">
        <input
          type="checkbox"
          checked={acceptsHealthData}
          disabled={disabled}
          onChange={(event) => onChangeHealthData(event.target.checked)}
          className="mt-0.5 h-4 w-4 shrink-0 accent-[rgb(var(--accent))]"
        />
        <span>
          J'accepte explicitement que mes <strong>données de santé</strong>{" "}
          (fréquence cardiaque, sommeil, poids, etc.) soient traitées pour
          personnaliser mon entraînement et alimenter le coach. Je peux retirer
          ce consentement à tout moment depuis mon profil.
        </span>
      </label>
    </div>
  );
}
