import LegalLayout from "../components/LegalLayout";
import { LEGAL_DOCS } from "../legal";

export default function MentionsLegales() {
  return <LegalLayout doc={LEGAL_DOCS["mentions-legales"]} />;
}
