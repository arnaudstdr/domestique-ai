import { Link, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { Eye, Megaphone, ShieldCheck, UserRound, Users, X } from "lucide-react";
import BottomNav from "./components/BottomNav";
import AnnouncementBanner from "./components/AnnouncementBanner";
import ConsentGate from "./components/ConsentGate";
import EmailVerificationBanner from "./components/EmailVerificationBanner";
import OnboardingTour from "./components/OnboardingTour";
import Dashboard from "./pages/Dashboard";
import Activities from "./pages/Activities";
import ActivityDetail from "./pages/ActivityDetail";
import Morning from "./pages/Morning";
import Coach from "./pages/Coach";
import Plan from "./pages/Plan";
import Profil from "./pages/Profil";
import Tendances from "./pages/Tendances";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import AcceptInvite from "./pages/AcceptInvite";
import Reconnect from "./pages/Reconnect";
import SetupTwoFactor from "./pages/SetupTwoFactor";
import VerifyEmail from "./pages/VerifyEmail";
import ForgotPassword from "./pages/ForgotPassword";
import ResetPassword from "./pages/ResetPassword";
import Roster from "./pages/Roster";
import Prescribe from "./pages/Prescribe";
import Feedback from "./pages/Feedback";
import Admin from "./pages/Admin";
import Cgu from "./pages/Cgu";
import Confidentialite from "./pages/Confidentialite";
import MentionsLegales from "./pages/MentionsLegales";
import NotFound from "./pages/NotFound";
import { clearViewingAthlete } from "./api/client";
import { MeProvider, useMe } from "./hooks/useMe";
import { useViewing } from "./hooks/useViewing";

function ViewingBanner({ name }: { name: string | null }) {
  const navigate = useNavigate();

  function leave() {
    clearViewingAthlete();
    navigate("/roster");
  }

  return (
    <div className="sticky top-0 z-[1200] bg-accent/15 backdrop-blur-xl border-b border-accent/30 pt-[env(safe-area-inset-top)]">
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-2 px-4 py-2">
        <span className="flex min-w-0 items-center gap-2 text-xs text-accent">
          <Eye className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
          <span className="truncate">
            Vue athlète · <strong>{name || "athlète"}</strong> (lecture seule)
          </span>
        </span>
        <button
          type="button"
          onClick={leave}
          className="btn-ghost flex shrink-0 items-center gap-1.5 px-3 py-1.5 text-xs"
        >
          <X className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          Quitter
        </button>
      </div>
    </div>
  );
}

function AuthenticatedLayout() {
  // Le provider est monté au niveau de la coquille authentifiée : un seul
  // appel `/me` partagé par l'en-tête et les pages, et un fetch frais à
  // chaque connexion (la coquille remonte après login).
  return (
    <MeProvider>
      <AuthedShell />
    </MeProvider>
  );
}

function AuthedShell() {
  const me = useMe();
  const viewing = useViewing();
  const isCoach = me?.role === "coach";
  const isAdmin = me?.role === "admin";

  return (
    <div className="min-h-screen bg-surface text-fg">
      <ConsentGate />
      <OnboardingTour />
      {viewing && <ViewingBanner name={viewing.name} />}
      <header
        className={`sticky top-0 z-[1100] bg-surface/70 backdrop-blur-xl
                   border-b border-border/[0.06] ${viewing ? "" : "pt-[env(safe-area-inset-top)]"}
                   shadow-[0_1px_0_0_rgb(255_255_255/0.03)]
                   will-change-transform [transform:translateZ(0)]
                   [-webkit-backface-visibility:hidden]`}
      >
        <div className="mx-auto max-w-3xl px-4 py-3 flex items-center justify-between">
          <h1 className="flex items-center gap-2.5 font-display text-[17px] font-extrabold tracking-tight">
            <img
              src="/icon-48.png"
              alt=""
              aria-hidden="true"
              className="h-7 w-7 rounded-lg ring-1 ring-border/10 shadow-card"
            />
            <span>
              Domestique<span className="text-accent">AI</span>
            </span>
          </h1>
          <div className="flex items-center gap-2">
            {isAdmin && !viewing && (
              <Link
                to="/admin"
                aria-label="Administration"
                title="Administration — comptes, retours, réglages"
                className="grid h-9 w-9 place-items-center rounded-xl text-fg-soft
                           border border-border/[0.06] bg-overlay/[0.03]
                           hover:text-accent hover:border-accent/40 transition-colors"
              >
                <ShieldCheck className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
              </Link>
            )}
            {isCoach && !viewing && (
              <Link
                to="/roster"
                aria-label="Roster"
                title="Roster — mes athlètes"
                className="grid h-9 w-9 place-items-center rounded-xl text-fg-soft
                           border border-border/[0.06] bg-overlay/[0.03]
                           hover:text-accent hover:border-accent/40 transition-colors"
              >
                <Users className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
              </Link>
            )}
            {!viewing && (
              <Link
                to="/feedback"
                aria-label="Donner mon avis"
                title="Donner mon avis"
                className="grid h-9 w-9 place-items-center rounded-xl text-fg-soft
                           border border-border/[0.06] bg-overlay/[0.03]
                           hover:text-accent hover:border-accent/40 transition-colors"
              >
                <Megaphone className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
              </Link>
            )}
            {!viewing && (
              <Link
                to="/profil"
                aria-label="Profil"
                title="Profil & paramètres"
                className="grid h-9 w-9 place-items-center overflow-hidden rounded-xl text-fg-soft
                           border border-border/[0.06] bg-overlay/[0.03]
                           hover:text-accent hover:border-accent/40 transition-colors"
              >
                {me?.avatar_url ? (
                  <img
                    src={me.avatar_url}
                    alt=""
                    aria-hidden="true"
                    className="h-full w-full object-cover"
                  />
                ) : (
                  <UserRound className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
                )}
              </Link>
            )}
          </div>
        </div>
      </header>
      <EmailVerificationBanner />
      <AnnouncementBanner />
      <main className="mx-auto max-w-3xl px-4 pt-4 pb-24">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/activites" element={<Activities />} />
          <Route path="/activites/:id" element={<ActivityDetail />} />
          <Route path="/sante" element={<Morning />} />
          <Route path="/matin" element={<Navigate to="/sante" replace />} />
          <Route path="/coach" element={<Coach />} />
          <Route path="/plan" element={<Plan />} />
          <Route path="/tendances" element={<Tendances />} />
          <Route path="/profil" element={<Profil />} />
          <Route path="/roster" element={<Roster />} />
          <Route path="/prescrire" element={<Prescribe />} />
          <Route path="/feedback" element={<Feedback />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <BottomNav viewing={!!viewing} />
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/accept-invite" element={<AcceptInvite />} />
      <Route path="/reconnect" element={<Reconnect />} />
      <Route path="/setup-2fa" element={<SetupTwoFactor />} />
      <Route path="/verify-email" element={<VerifyEmail />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/mentions-legales" element={<MentionsLegales />} />
      <Route path="/cgu" element={<Cgu />} />
      <Route path="/confidentialite" element={<Confidentialite />} />
      <Route path="/*" element={<AuthenticatedLayout />} />
    </Routes>
  );
}
