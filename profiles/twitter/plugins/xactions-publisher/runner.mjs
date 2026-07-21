import path from 'node:path';
import { pathToFileURL } from 'node:url';

const requiredEnv = (name) => {
  const value = (process.env[name] || '').trim();
  if (!value) throw new Error(`Configuration manquante: ${name}`);
  return value;
};

const sanitize = (message, secrets) => {
  let safe = String(message || 'Erreur XActions inconnue');
  for (const secret of secrets) {
    if (secret) safe = safe.split(secret).join('[SECRET]');
  }
  return safe.slice(0, 500);
};

let authToken = '';
let ct0 = '';

try {
  const root = path.resolve(requiredEnv('XACTIONS_ROOT'));
  authToken = requiredEnv('XACTIONS_AUTH_TOKEN');
  ct0 = requiredEnv('XACTIONS_CT0');
  const expectedUsername = requiredEnv('XACTIONS_EXPECTED_USERNAME')
    .replace(/^@/, '')
    .toLowerCase();

  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const rawInput = Buffer.concat(chunks).toString('utf8');
  const payload = JSON.parse(rawInput);
  const action = String(payload?.action || 'post');
  const text = payload?.text;
  const tweetId = String(payload?.tweet_id || '');
  const username = String(payload?.username || '').replace(/^@/, '').toLowerCase();

  if (!['post', 'reply', 'like', 'follow'].includes(action)) {
    throw new Error(`Action XActions non autorisée: ${action}`);
  }
  if (['post', 'reply'].includes(action)) {
    if (typeof text !== 'string' || text.length === 0 || text.length > 280) {
      throw new Error('Texte invalide ou supérieur à 280 caractères');
    }
  }
  if (['reply', 'like'].includes(action) && !/^\d+$/.test(tweetId)) {
    throw new Error('Identifiant de post X invalide');
  }
  if (action === 'follow' && !/^[a-z0-9_]{1,15}$/i.test(username)) {
    throw new Error('Nom de compte X invalide');
  }

  const moduleUrl = (relativePath) =>
    pathToFileURL(path.join(root, 'src', 'scrapers', 'twitter', 'http', relativePath)).href;
  const [{ TwitterHttpClient }, { TwitterAuth }] = await Promise.all([
    import(moduleUrl('client.js')),
    import(moduleUrl('auth.js')),
  ]);

  const cookies = `auth_token=${authToken}; ct0=${ct0}`;
  const auth = new TwitterAuth();
  const user = await auth.loginWithCookies(cookies);
  const actualUsername = String(user?.username || '').replace(/^@/, '').toLowerCase();
  if (!actualUsername || actualUsername !== expectedUsername) {
    throw new Error(
      `Compte X inattendu: @${actualUsername || 'inconnu'} au lieu de @${expectedUsername}`,
    );
  }

  const client = new TwitterHttpClient({
    cookies,
    rateLimitStrategy: 'error',
    maxRetries: 0,
  });
  if (action === 'post' || action === 'reply') {
    const actions = await import(moduleUrl('actions.js'));
    const result =
      action === 'post'
        ? await actions.postTweet(client, text)
        : await actions.replyToTweet(client, tweetId, text);
    const createdTweetId =
      result?.rest_id || result?.legacy?.id_str || result?.tweet?.rest_id || null;
    if (!createdTweetId) {
      throw new Error("XActions n'a retourné aucun identifiant de post");
    }
    process.stdout.write(
      JSON.stringify({
        success: true,
        action,
        account: `@${actualUsername}`,
        tweet_id: String(createdTweetId),
        url: `https://x.com/${actualUsername}/status/${createdTweetId}`,
      }),
    );
  } else if (action === 'like') {
    const { likeTweet } = await import(moduleUrl('engagement.js'));
    await likeTweet(client, tweetId);
    process.stdout.write(
      JSON.stringify({
        success: true,
        action,
        account: `@${actualUsername}`,
        tweet_id: tweetId,
        url: `https://x.com/i/web/status/${tweetId}`,
      }),
    );
  } else {
    const { followByUsername } = await import(moduleUrl('engagement.js'));
    await followByUsername(client, username);
    process.stdout.write(
      JSON.stringify({
        success: true,
        action,
        account: `@${actualUsername}`,
        target_account: `@${username}`,
      }),
    );
  }
} catch (error) {
  process.stdout.write(
    JSON.stringify({
      success: false,
      error: sanitize(error?.message || error, [authToken, ct0]),
    }),
  );
  process.exitCode = 1;
}
