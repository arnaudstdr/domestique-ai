/**
 * Bus d'événements du tuto d'onboarding (cf. `components/OnboardingTour.tsx`).
 *
 * Les pages qui réalisent une action guidée (profil enregistré, Garmin connecté,
 * synchro santé) émettent un signal ; le composant du tuto écoute, revérifie
 * l'état réel côté API et avance tout seul. Le bouton « Revoir le guide » de la
 * page Profil déclenche la relance pour la session courante.
 */

export type OnboardingSignal = "profile-saved" | "garmin-connected" | "health-synced";

const SIGNAL_EVENT = "domestique:onboarding-event";
const START_EVENT = "domestique:onboarding-start";

/** Signale au tuto qu'une action guidée vient d'être complétée. */
export function emitOnboardingSignal(signal: OnboardingSignal): void {
  window.dispatchEvent(new CustomEvent<OnboardingSignal>(SIGNAL_EVENT, { detail: signal }));
}

/** Relance le tuto pour la session courante (ignore le flag « déjà vu »). */
export function startOnboardingTour(): void {
  window.dispatchEvent(new Event(START_EVENT));
}

export function subscribeOnboardingSignals(
  handler: (signal: OnboardingSignal) => void,
): () => void {
  const listener = (event: Event) => handler((event as CustomEvent<OnboardingSignal>).detail);
  window.addEventListener(SIGNAL_EVENT, listener);
  return () => window.removeEventListener(SIGNAL_EVENT, listener);
}

export function subscribeOnboardingStart(handler: () => void): () => void {
  window.addEventListener(START_EVENT, handler);
  return () => window.removeEventListener(START_EVENT, handler);
}
