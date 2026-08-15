/* One shared Supabase client for the whole dashboard.
 *
 * auth.js, positions.js and alert-prefs.js each used to call
 * window.supabase.createClient(cfg.url, cfg.key) independently — three
 * clients, all pointed at the same project, all reading/writing the same
 * localStorage-persisted session. Harmless (they agree on who is signed in)
 * but Supabase logs a "Multiple GoTrueClient instances detected" warning for
 * every extra one, which is noise a reader's console doesn't need.
 *
 * Loaded once, right after supabase.min.js and before any file that uses it.
 * Fail-open, same as every other auth-adjacent file here: if SUPABASE_CONFIG
 * wasn't baked or the library didn't load, window.SMSupabase stays unset and
 * every consumer's own guard (`if (!cfg || ... || !window.SMSupabase) return;`)
 * leaves the page untouched. */
(function () {
  var cfg = window.SUPABASE_CONFIG;
  if (!cfg || !cfg.url || !cfg.key || !window.supabase) return;
  window.SMSupabase = window.supabase.createClient(cfg.url, cfg.key);
})();
