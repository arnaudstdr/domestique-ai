import { useCallback, useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { driver, type Driver, type DriveStep } from "driver.js";
import "driver.js/dist/driver.css";
import { api, ApiError } from "../api/client";
import type { GarminStatus, HealthSourcesResponse, Profile } from "../api/types";
import { useMe, useMeRefresh } from "../hooks/useMe";
import { useToast } from "../hooks/useToast";
import { useViewing } from "../hooks/useViewing";
import { subscribeOnboardingSignals, subscribeOnboardingStart } from "../lib/onboarding";

type StepKey = "profile" | "garmin" | "health" | "totp";
type TourStep = { kind: "welcome" } | { kind: StepKey } | { kind: "finish" };

const STEP_KEYS: StepKey[] = ["profile", "garmin", "health", "totp"];

function isPendingKind(kind: TourStep["kind"]): kind is StepKey {
  return kind === "profile" || kind === "garmin" || kind === "health" || kind === "totp";
}

function scrollToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/**
 * Tuto interactif des nouveaux comptes : profil → Garmin → 1re synchro santé.
 *
 * Carte flottante driver.js non modale (pas d'ancrage ni de surbrillance), posée
 * au-dessus de la barre de navigation. L'avancement d'une étape est dérivé des
 * données réelles (`GET /api/profile`, `GET /api/garmin/status`,
 * `GET /api/morning/sources`) : le tour ne se poursuit qu'une fois l'action
 * réellement constatée côté API. Seuls « terminé » / « passé » sont persistés
 * côté serveur (`POST /api/auth/me/onboarding`), ce qui le rend cohérent entre
 * appareils.
 */
export default function OnboardingTour() {
  const me = useMe();
  const refreshMe = useMeRefresh();
  const viewing = useViewing();
  const navigate = useNavigate();
  const location = useLocation();
  const { push } = useToast();

  const driverRef = useRef<Driver | null>(null);
  // `me` est lu via une ref pour que `fetchProgress` (et l'intervalle de
  // revérification) voie toujours l'état courant sans se recréer à chaque refresh.
  const meRef = useRef(me);
  meRef.current = me;
  const sequenceRef = useRef<TourStep[]>([]);
  const doneRef = useRef<Set<StepKey>>(new Set());
  const intervalRef = useRef<number | undefined>(undefined);
  const finishedRef = useRef(false);
  const launchedRef = useRef(false);
  const launchingRef = useRef(false);
  const refreshingRef = useRef(false);
  const teardownRef = useRef(false);

  const fetchProgress = useCallback(async (): Promise<Record<StepKey, boolean> | null> => {
    const [profile, garmin, sources] = await Promise.allSettled([
      api.profile.get() as Promise<Profile | null>,
      api.garmin.status() as Promise<GarminStatus>,
      api.morning.sources() as Promise<HealthSourcesResponse>,
    ]);
    // Hors ligne / API indisponible : on ne lance rien, on retentera au montage.
    if (
      profile.status === "rejected" &&
      garmin.status === "rejected" &&
      sources.status === "rejected"
    ) {
      return null;
    }
    return {
      profile: profile.status === "fulfilled" && profile.value !== null,
      garmin: garmin.status === "fulfilled" && garmin.value.connected,
      health: sources.status === "fulfilled" && sources.value.garmin.last_sync_at !== null,
      totp: meRef.current?.totp_enabled === true,
    };
  }, []);

  const refreshAndAdvance = useCallback(async () => {
    const d = driverRef.current;
    if (!d?.isActive() || refreshingRef.current) return;
    refreshingRef.current = true;
    try {
      const progress = await fetchProgress();
      if (!progress) return;
      for (const key of STEP_KEYS) {
        if (progress[key]) doneRef.current.add(key);
      }
      // Avance tant que l'étape courante est faite (plusieurs étapes peuvent se
      // valider d'un coup si l'utilisateur a tout configuré entre-temps).
      for (let guard = 0; guard <= STEP_KEYS.length; guard += 1) {
        const index = d.getActiveIndex();
        if (index === undefined) break;
        const step = sequenceRef.current[index];
        if (!step || !isPendingKind(step.kind) || !doneRef.current.has(step.kind)) break;
        d.moveNext();
      }
    } finally {
      refreshingRef.current = false;
    }
  }, [fetchProgress]);

  const runHealthSync = useCallback(
    async (button: HTMLButtonElement) => {
      if (button.disabled) return;
      button.disabled = true;
      button.textContent = "Synchronisation en cours…";
      try {
        const result = await api.garmin.healthSync(7);
        if (result.success) {
          button.textContent = "Synchronisé";
          push(result.message || "Données santé importées.", "success");
          await refreshAndAdvance();
        } else {
          button.disabled = false;
          button.textContent = "Réessayer la synchro";
          push(result.message || "Aucune donnée santé récupérée.", "error");
        }
      } catch (err) {
        button.disabled = false;
        button.textContent = "Réessayer la synchro";
        push(err instanceof ApiError ? err.message : "Sync santé impossible.", "error");
      }
    },
    [push, refreshAndAdvance],
  );

  const wireActions = useCallback(
    (wrapper: HTMLElement) => {
      wrapper
        .querySelector<HTMLButtonElement>('[data-onboarding-action="profile"]')
        ?.addEventListener("click", () => {
          navigate("/profil");
          window.setTimeout(() => scrollToSection("profil-infos-perso"), 250);
        });
      wrapper
        .querySelector<HTMLButtonElement>('[data-onboarding-action="garmin"]')
        ?.addEventListener("click", () => {
          navigate("/profil");
          window.setTimeout(() => scrollToSection("profil-garmin"), 250);
        });
      wrapper
        .querySelector<HTMLButtonElement>('[data-onboarding-action="health"]')
        ?.addEventListener("click", (event) => {
          void runHealthSync(event.currentTarget as HTMLButtonElement);
        });
      wrapper
        .querySelector<HTMLButtonElement>('[data-onboarding-action="totp"]')
        ?.addEventListener("click", () => {
          navigate("/setup-2fa?next=/");
        });
    },
    [navigate, runHealthSync],
  );

  const buildStep = useCallback(
    (step: TourStep, index: number, total: number): DriveStep => {
      const number = index; // 0 = accueil, les étapes guidées commencent à 1.
      switch (step.kind) {
        case "welcome":
          return {
            popover: {
              title: "Bienvenue dans DomestiqueAI",
              description:
                "<p>Quelques étapes rapides pour que tes activités, ta charge " +
                "et tes zones soient calculées correctement — et pour sécuriser " +
                "ton compte.</p>" +
                "<p class=\"onboarding-hint\">Tu peux fermer ce guide à tout moment — " +
                "il se relance depuis la page Profil.</p>",
              nextBtnText: "C'est parti",
              showProgress: false,
            },
          };
        case "profile":
          return {
            popover: {
              title: `Étape ${number}/${total} · Complète tes infos perso`,
              description:
                "<p>FTP, FC de repos, FC max et sexe pilotent le calcul de charge " +
                "(TSS), les zones cardiaques et le ratio poids/puissance.</p>" +
                '<button type="button" data-onboarding-action="profile" ' +
                'class="onboarding-cta">Ouvrir mon profil</button>' +
                '<p class="onboarding-hint">La carte « Infos perso » est en haut de ' +
                "la page.</p>",
              nextBtnText: "Suivant",
              showProgress: false,
              // On ne passe pas à l'étape suivante tant que l'action n'est pas
              // constatée côté API (bouton et flèche droite neutralisés) ;
              // l'avancement est automatique via `moveNext()`.
              disableButtons: ["next"],
            },
          };
        case "garmin":
          return {
            popover: {
              title: `Étape ${number}/${total} · Connecte Garmin Connect`,
              description:
                "<p>Garmin importe automatiquement tes activités et tes nuits " +
                "(sommeil, HRV, FC de repos).</p>" +
                '<button type="button" data-onboarding-action="garmin" ' +
                'class="onboarding-cta">Voir la connexion Garmin</button>' +
                '<p class="onboarding-hint">La carte « Garmin Connect » est juste ' +
                "sous les infos perso.</p>",
              nextBtnText: "Suivant",
              showProgress: false,
              disableButtons: ["next"],
            },
          };
        case "health":
          return {
            popover: {
              title: `Étape ${number}/${total} · Première synchro santé`,
              description:
                "<p>Récupère tes dernières nuits depuis Garmin Health pour remplir " +
                "la page Santé (readiness, HRV, sommeil).</p>" +
                '<button type="button" data-onboarding-action="health" ' +
                'class="onboarding-cta">Synchroniser maintenant</button>',
              nextBtnText: "Suivant",
              showProgress: false,
              disableButtons: ["next"],
            },
          };
        case "totp":
          return {
            popover: {
              title: `Étape ${number}/${total} · Sécurise ton compte`,
              description:
                "<p>La double authentification (2FA) protège ton compte même si " +
                "ton mot de passe fuite. Active-la maintenant : le bouton " +
                "ci-dessous ouvre l'assistant (application d'authentification " +
                "puis codes de secours).</p>" +
                '<button type="button" data-onboarding-action="totp" ' +
                'class="onboarding-cta">Activer la 2FA</button>' +
                '<p class="onboarding-hint">Tu peux aussi la retrouver dans ' +
                "Profil → Sécurité.</p>",
              nextBtnText: "Suivant",
              showProgress: false,
              disableButtons: ["next"],
            },
          };
        case "finish":
          return {
            popover: {
              title: "Tout est prêt",
              description:
                "<p>Ton profil, Garmin et tes premières données santé sont en " +
                "place : la charge peut être calculée et le coach peut te proposer " +
                "un plan adapté.</p>",
              doneBtnText: "Terminer",
              showProgress: false,
            },
          };
      }
    },
    [],
  );

  const launchInner = useCallback(
    async (force: boolean) => {
      if (driverRef.current?.isActive()) return;
      if (launchedRef.current && !force) return;
      launchedRef.current = true;
      const progress = await fetchProgress();
      if (!progress) return;
      doneRef.current = new Set(STEP_KEYS.filter((key) => progress[key]));
      const pending = STEP_KEYS.filter((key) => !doneRef.current.has(key));
      if (pending.length === 0) {
        if (force) {
          push("Le guide est déjà complet : profil, Garmin et santé sont configurés.", "info");
        } else if (!finishedRef.current) {
          // Tout était déjà fait : on marque le guide comme terminé en silence
          // pour ne pas revérifier à chaque montage.
          finishedRef.current = true;
          api.auth
            .setOnboarding("complete")
            .then(() => refreshMe())
            .catch(() => undefined);
        }
        return;
      }
      sequenceRef.current = [
        { kind: "welcome" },
        ...pending.map((kind): TourStep => ({ kind })),
        { kind: "finish" },
      ];
      finishedRef.current = false;
      const total = pending.length;
      const steps = sequenceRef.current.map((step, index) => buildStep(step, index, total));

      const d = driver({
        steps,
        animate: false,
        overlayOpacity: 0,
        overlayClickBehavior: "none",
        allowClose: true,
        allowScroll: true,
        smoothScroll: false,
        popoverClass: "onboarding-popover",
        nextBtnText: "Suivant",
        prevBtnText: "Retour",
        doneBtnText: "Terminer",
        closeBtnLabel: "Fermer le guide",
        onPopoverRender: (popover) => {
          wireActions(popover.wrapper);
        },
        onDestroyed: (_element, _step, opts) => {
          window.clearInterval(intervalRef.current);
          driverRef.current = null;
          if (teardownRef.current || finishedRef.current) return;
          finishedRef.current = true;
          const lastIndex = sequenceRef.current.length - 1;
          const activeIndex = opts.state.activeIndex;
          const completed = typeof activeIndex === "number" && activeIndex >= lastIndex;
          if (!completed) {
            push("Guide fermé — tu peux le relancer depuis la page Profil.", "info");
          }
          api.auth
            .setOnboarding(completed ? "complete" : "dismiss")
            .then(() => refreshMe())
            .catch(() => undefined);
        },
      });
      driverRef.current = d;
      d.drive();
      // Tour non modal : la classe `driver-active` (posée par `drive()`) porte
      // la règle `.driver-active * { pointer-events: none }` qui neutralise tout
      // le contenu de la page. On la retire — driver.js ne s'en sert pas en JS,
      // et l'overlay reste neutralisé par le CSS d'`index.css`.
      document.body.classList.remove("driver-active");
      // driver.js piège aussi la touche Tab dans la carte (comportement modal) :
      // le contenu doit rester utilisable au clavier pendant le tuto, on retire
      // seulement ce listener (Échap / flèches restent gérés).
      const events = d.getState("__events") as
        | { onKeydown?: (event: KeyboardEvent) => void }
        | undefined;
      if (events?.onKeydown) window.removeEventListener("keydown", events.onKeydown);
      window.clearInterval(intervalRef.current);
      intervalRef.current = window.setInterval(() => {
        void refreshAndAdvance();
      }, 6000);
    },
    [buildStep, fetchProgress, push, refreshAndAdvance, refreshMe, wireActions],
  );

  const launch = useCallback(
    async (force: boolean) => {
      if (driverRef.current?.isActive() || launchingRef.current) return;
      launchingRef.current = true;
      try {
        await launchInner(force);
      } finally {
        launchingRef.current = false;
      }
    },
    [launchInner],
  );

  // Montage / démontage : le teardown ne doit pas marquer le guide comme passé
  // (ex. déconnexion pendant un tuto).
  useEffect(() => {
    teardownRef.current = false;
    return () => {
      teardownRef.current = true;
      window.clearInterval(intervalRef.current);
      driverRef.current?.destroy();
      driverRef.current = null;
    };
  }, []);

  // Lancement automatique : comptes athlète/coach, hors consultation, après le
  // portail de consentement. Pendant la période de grâce 2FA (nouveau compte),
  // le tuto démarre quand même — l'étape « Sécurise ton compte » guide vers
  // l'enrôlement. Sans grâce (admin, deadline passée), il attend la 2FA.
  useEffect(() => {
    if (!me || viewing) return;
    if (me.role === "admin" || me.is_bootstrap) return;
    if (me.onboarding_completed_at || me.onboarding_dismissed_at) return;
    if (me.has_password && !me.totp_enabled && !me.totp_grace_until) return;
    if (!me.terms_accepted_at || !me.health_consent_at) return;
    const timer = window.setTimeout(() => {
      void launch(false);
    }, 700);
    return () => window.clearTimeout(timer);
  }, [me, viewing, launch]);

  // Relance manuelle depuis la page Profil.
  useEffect(() => {
    return subscribeOnboardingStart(() => {
      void launch(true);
    });
  }, [launch]);

  // Revérifications : signal émis par une page, retour sur l'onglet, navigation.
  useEffect(() => {
    return subscribeOnboardingSignals(() => {
      void refreshAndAdvance();
    });
  }, [refreshAndAdvance]);

  useEffect(() => {
    const onFocus = () => {
      void refreshAndAdvance();
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refreshAndAdvance]);

  useEffect(() => {
    void refreshAndAdvance();
  }, [location.pathname, refreshAndAdvance]);

  return null;
}
