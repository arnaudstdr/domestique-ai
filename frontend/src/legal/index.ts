// Documents légaux publiés (Markdown importé brut, rendu par LegalLayout).
//
// ⚠️ La version ci-dessous doit rester alignée avec `LEGAL_VERSION` côté
// backend (`domestique_ai/legal.py`) : c'est elle qui est enregistrée comme
// preuve de consentement à l'inscription.

import cgu from "./cgu.md?raw";
import confidentialite from "./confidentialite.md?raw";
import mentionsLegales from "./mentions-legales.md?raw";

export const LEGAL_VERSION = "2026-10-beta";
export const LEGAL_CONTACT_EMAIL = "contact@domestique-ai.com";
export const LEGAL_UPDATED_AT = "3 octobre 2026";

export type LegalDocKey = "cgu" | "confidentialite" | "mentions-legales";

export interface LegalDoc {
  key: LegalDocKey;
  path: string;
  title: string;
  description: string;
  content: string;
}

export const LEGAL_DOCS: Record<LegalDocKey, LegalDoc> = {
  cgu: {
    key: "cgu",
    path: "/cgu",
    title: "Conditions générales d'utilisation",
    description:
      "Conditions générales d'utilisation de DomestiqueAI : accès, compte, usage acceptable, données de santé, responsabilité.",
    content: cgu,
  },
  confidentialite: {
    key: "confidentialite",
    path: "/confidentialite",
    title: "Politique de confidentialité",
    description:
      "Politique de confidentialité de DomestiqueAI : données traitées, finalités, destinataires, durées, droits RGPD.",
    content: confidentialite,
  },
  "mentions-legales": {
    key: "mentions-legales",
    path: "/mentions-legales",
    title: "Mentions légales",
    description:
      "Mentions légales du site domestique-ai.com : éditeur, hébergement, propriété intellectuelle.",
    content: mentionsLegales,
  },
};
