import { Link, Route, Routes } from "react-router-dom";
import { Settings } from "lucide-react";
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

function AuthenticatedLayout() {
  return (
    <div className="min-h-screen bg-surface text-gray-100">
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
        </Routes>
      </main>
      <BottomNav />
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/*" element={<AuthenticatedLayout />} />
    </Routes>
  );
}
