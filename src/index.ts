/**
 * Browser Use x agent de trading XRPL.
 *
 * Mon agent tourne sur des prix d'API, mais ce qui fait vraiment bouger les marchés,
 * c'est le contexte web : annonces, sentiment X, news. Browser Use est le chaînon
 * manquant : un agent navigateur autonome qui va chercher ce contexte et le remonte
 * à l'agent de trading. Un agent qui nourrit un agent.
 *
 * Prérequis : BROWSER_USE_API_KEY=bu_... dans l'environnement
 *   (clé depuis cloud.browser-use.com/settings ; jamais commit dans le repo)
 * Run : npm install && npm start
 */
import { BrowserUseClient } from "browser-use-sdk";

const apiKey = process.env.BROWSER_USE_API_KEY;
if (!apiKey) {
  throw new Error(
    "BROWSER_USE_API_KEY manquant — récupère une clé bu_... sur cloud.browser-use.com/settings",
  );
}

const client = new BrowserUseClient({ apiKey });

/** Point d'accroche : là où le sentiment récupéré rejoint l'agent de trading. */
function feedTradingAgent(sentiment: unknown): void {
  console.log("\n=== Sentiment XRP transmis à l'agent de trading ===\n");
  console.log(sentiment);
}

async function main(): Promise<void> {
  const task = await client.tasks.createTask({
    task:
      "Cherche sur le web et sur X/Twitter le sentiment et les news récentes " +
      "(dernières 24h) sur XRP et le XRP Ledger. Renvoie les 5 éléments les plus " +
      "marquants, chacun avec : source, titre, et un tag bullish / bearish / neutre.",
  });

  console.log("Agent navigateur Browser Use lancé, récupération du contexte marché…");

  const result = await task.complete();
  feedTradingAgent(result.output);
}

main().catch((err) => {
  console.error("Échec du run Browser Use :", err);
  process.exit(1);
});
