import { Link, Route, Routes, useNavigate } from "react-router-dom";
import { Eye, Settings, Users, X } from "lucide-react";
import BottomNav from "./components/BottomNav";
import Dashboard from "./pages/Dashboard";
import Activities from "./pages/Activities";
import ActivityDetail from "./pages/ActivityDetail";
import Morning from "./pages/Morning";
import Coach from "./pages/Coach";
import Plan from "./pages/Plan";
import Profil from "./pages/Profil";
import Tendances from "./pages/Tendances";
import Login from "./pages/Login";
import AcceptInvite from "./pages/AcceptInvite";
import Roster from "./pages/Roster";
import Prescribe from "./pages/Prescribe";
import { clearViewingAthlete } from "./api/client";
import { useMe } from "./hooks/useMe";
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
  const me = useMe();
  const viewing = useViewing();
  const isCoach = me?.role === "coach";

  return (
    <div className="min-h-screen bg-surface text-gray-100">
      {viewing && <ViewingBanner name={viewing.name} />}
      <header
        className="sticky top-0 z-[1100] bg-surface/70 backdrop-blur-xl
                   border-b border-white/[0.06] pt-[env(safe-area-inset-top)]
                   shadow-[0_1px_0_0_rgb(255_255_255/0.03)]"
      >
        <div className="mx-auto max-w-3xl px-4 py-3 flex items-center justify-between">
          <h1 className="flex items-center gap-2.5 font-display text-[17px] font-extrabold tracking-tight">
            <img
              src="/icon-48.png"
              alt=""
              aria-hidden="true"
              className="h-7 w-7 rounded-lg ring-1 ring-white/10 shadow-card"
            />
            <span>
              Domestique<span className="text-accent">AI</span>
            </span>
          </h1>
          <div className="flex items-center gap-2">
            {isCoach && !viewing && (
              <Link
                to="/roster"
                aria-label="Roster"
                title="Roster — mes athlètes"
                className="grid h-9 w-9 place-items-center rounded-xl text-gray-300
                           border border-white/[0.06] bg-white/[0.03]
                           hover:text-accent hover:border-accent/40 transition-colors"
              >
                <Users className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
              </Link>
            )}
            {!viewing && (
              <Link
                to="/profil"
                aria-label="Profil"
                title="Profil & paramètres"
                className="grid h-9 w-9 place-items-center rounded-xl text-gray-300
                           border border-white/[0.06] bg-white/[0.03]
                           hover:text-accent hover:border-accent/40 transition-colors"
              >
                <Settings className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
              </Link>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 pt-4 pb-24">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/activites" element={<Activities />} />
          <Route path="/activites/:id" element={<ActivityDetail />} />
          <Route path="/matin" element={<Morning />} />
          <Route path="/coach" element={<Coach />} />
          <Route path="/plan" element={<Plan />} />
          <Route path="/tendances" element={<Tendances />} />
          <Route path="/profil" element={<Profil />} />
          <Route path="/roster" element={<Roster />} />
          <Route path="/prescrire" element={<Prescribe />} />
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
      <Route path="/accept-invite" element={<AcceptInvite />} />
      <Route path="/*" element={<AuthenticatedLayout />} />
    </Routes>
  );
}
