# hermes-agent-x-browser-use

Intégration [Browser Use](https://browser-use.com) dans mon agent de trading **XRPL**.

## Le pourquoi

L'agent de trading tourne sur des prix d'API. Mais ce qui fait vraiment bouger les
marchés, c'est le contexte web : annonces, sentiment sur X, news. Browser Use est le
chaînon manquant — un agent navigateur autonome qui va chercher ce contexte et le
remonte à l'agent de trading. **Un agent qui nourrit un agent.**

Ce repo est le premier maillon : un run Browser Use qui récupère le sentiment/news XRP
en langage naturel, prêt à être branché sur la boucle de décision de l'agent
(`feedTradingAgent`).

## Utilisation

```bash
npm install
export BROWSER_USE_API_KEY=bu_...   # clé depuis cloud.browser-use.com/settings
npm start
```

## Prochaine étape

Passer d'un run one-shot à un flux continu : sortie structurée (schéma Zod), scoring
du sentiment, et intégration dans la boucle d'autonomie de l'agent (Tide, hackathon
Make Waves XRPL).
