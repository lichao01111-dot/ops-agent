
// Login Page Component
const LoginPage = ({ onLogin, theme }) => {
  const [username, setUsername] = React.useState('');
  const [password, setPassword] = React.useState('');
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState('');
  const [showPass, setShowPass] = React.useState(false);
  const [step, setStep] = React.useState('login'); // login | mfa

  const t = theme || {};
  const bg = t.bg || '#070d1a';
  const surface = t.surface || '#0d1628';
  const accent = t.accent || '#3b82f6';
  const border = t.border || 'rgba(99,140,210,0.18)';
  const text = t.text || '#e2e8f0';
  const subtext = t.subtext || '#94a3b8';

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!username || !password) {setError('请输入用户名和密码 / Please enter credentials');return;}
    setError('');
    setLoading(true);
    // Skip MFA for now — wire it back when backend supports /api/auth/mfa.
    try {
      if (window.JarvisAPI && window.JarvisAPI.login) {
        const data = await window.JarvisAPI.login(username, password);
        setLoading(false);
        onLogin(data.user);
        return;
      }
      // Fallback: legacy demo behaviour for offline/mock mode.
      setTimeout(() => {
        setLoading(false);
        if (step === 'login') setStep('mfa'); else onLogin({ username });
      }, 1200);
    } catch (err) {
      setLoading(false);
      const msg = (err && err.body && err.body.detail) || (err && err.message) || '登录失败 / Login failed';
      setError(`${msg}（提示：admin/admin、operator/operator、viewer/viewer）`);
    }
  };

  return (
    <div style={{
      width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: bg, position: 'relative', overflow: 'hidden', fontFamily: "'Inter', 'Noto Sans SC', sans-serif"
    }}>
      {/* Background grid */}
      <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', opacity: 0.04 }} xmlns="http://www.w3.org/2000/svg">
        <defs>
          <pattern id="grid" width="48" height="48" patternUnits="userSpaceOnUse">
            <path d="M 48 0 L 0 0 0 48" fill="none" stroke={accent} strokeWidth="0.5" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)" />
      </svg>

      {/* Glow orbs */}
      <div style={{ position: 'absolute', top: '20%', left: '15%', width: 400, height: 400, borderRadius: '50%', background: `radial-gradient(circle, ${accent}22 0%, transparent 70%)`, pointerEvents: 'none' }} />
      <div style={{ position: 'absolute', bottom: '20%', right: '15%', width: 300, height: 300, borderRadius: '50%', background: `radial-gradient(circle, ${accent}18 0%, transparent 70%)`, pointerEvents: 'none' }} />

      {/* Card */}
      <div style={{
        width: 420, background: surface, borderRadius: 16, border: `1px solid ${border}`,
        padding: '40px 40px 36px', position: 'relative', zIndex: 1,
        boxShadow: `0 24px 80px rgba(0,0,0,0.5), 0 0 0 1px ${border}`
      }}>
        {/* Logo */}
        <div style={{ marginBottom: 32, textAlign: 'center' }}>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 12 }}>
            <div style={{
              width: 42, height: 42, borderRadius: 10, background: `linear-gradient(135deg, ${accent}, ${accent}99)`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              boxShadow: `0 0 20px ${accent}44`
            }}>
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round">
                <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
              </svg>
            </div>
            <div style={{ textAlign: 'left' }}>
              <div style={{ color: text, fontWeight: 700, fontSize: 18, letterSpacing: '-0.02em' }}>JARVIS</div>
              <div style={{ color: subtext, fontSize: 11, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Enterprise AI · 企业智能平台</div>
            </div>
          </div>
        </div>



        {step === 'login' ? <form onSubmit={handleSubmit}>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', color: subtext, fontSize: 12, marginBottom: 6, fontWeight: 500 }}>用户名 / Username</label>
              <input
              value={username} onChange={(e) => setUsername(e.target.value)}
              placeholder="请输入工号或用户名"
              style={{
                width: '100%', background: `${bg}cc`, border: `1px solid ${border}`, borderRadius: 8,
                padding: '11px 14px', color: text, fontSize: 14, outline: 'none', boxSizing: 'border-box',
                transition: 'border-color 0.2s'
              }}
              onFocus={(e) => e.target.style.borderColor = accent}
              onBlur={(e) => e.target.style.borderColor = border} />
            
            </div>
            <div style={{ marginBottom: 20 }}>
              <label style={{ display: 'block', color: subtext, fontSize: 12, marginBottom: 6, fontWeight: 500 }}>密码 / Password</label>
              <div style={{ position: 'relative' }}>
                <input
                type={showPass ? 'text' : 'password'} value={password} onChange={(e) => setPassword(e.target.value)}
                placeholder="请输入密码"
                style={{
                  width: '100%', background: `${bg}cc`, border: `1px solid ${border}`, borderRadius: 8,
                  padding: '11px 40px 11px 14px', color: text, fontSize: 14, outline: 'none', boxSizing: 'border-box'
                }}
                onFocus={(e) => e.target.style.borderColor = accent}
                onBlur={(e) => e.target.style.borderColor = border} />
              
                <button type="button" onClick={() => setShowPass(!showPass)} style={{
                position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)',
                background: 'none', border: 'none', cursor: 'pointer', color: subtext, padding: 0
              }}>
                  {showPass ? '🙈' : '👁'}
                </button>
              </div>
            </div>
            {error && <div style={{ color: '#f87171', fontSize: 12, marginBottom: 14 }}>{error}</div>}
            <button type="submit" disabled={loading} style={{
            width: '100%', background: loading ? `${accent}88` : `linear-gradient(135deg, ${accent}, ${accent}cc)`,
            border: 'none', borderRadius: 8, padding: '12px', color: 'white', fontSize: 14, fontWeight: 600,
            cursor: loading ? 'not-allowed' : 'pointer', letterSpacing: '0.02em',
            boxShadow: loading ? 'none' : `0 4px 20px ${accent}44`, transition: 'all 0.2s'
          }}>
              {loading ? '验证中... / Verifying...' : '登录 / Sign In'}
            </button>
          </form> :

        <form onSubmit={handleSubmit}>
            <div style={{ textAlign: 'center', marginBottom: 24 }}>
              <div style={{ fontSize: 32, marginBottom: 8 }}>🔐</div>
              <div style={{ color: text, fontWeight: 600, marginBottom: 4 }}>双因素验证 / 2FA Verification</div>
              <div style={{ color: subtext, fontSize: 13 }}>请输入您的动态令牌 / Enter your OTP token</div>
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginBottom: 20 }}>
              {[0, 1, 2, 3, 4, 5].map((i) =>
            <input key={i} maxLength={1} style={{
              width: 44, height: 52, textAlign: 'center', background: `${bg}cc`, border: `1px solid ${border}`,
              borderRadius: 8, color: text, fontSize: 20, fontWeight: 700, outline: 'none',
              boxSizing: 'border-box'
            }}
            onFocus={(e) => e.target.style.borderColor = accent}
            onBlur={(e) => e.target.style.borderColor = border}
            onChange={(e) => {if (e.target.value && e.target.nextSibling) e.target.nextSibling.focus();}} />

            )}
            </div>
            <button type="submit" disabled={loading} style={{
            width: '100%', background: `linear-gradient(135deg, ${accent}, ${accent}cc)`,
            border: 'none', borderRadius: 8, padding: '12px', color: 'white', fontSize: 14, fontWeight: 600,
            cursor: 'pointer', boxShadow: `0 4px 20px ${accent}44`
          }}>
              {loading ? '验证中...' : '确认登录 / Confirm'}
            </button>
            <div style={{ textAlign: 'center', marginTop: 12 }}>
              <button type="button" onClick={() => setStep('login')} style={{
              background: 'none', border: 'none', color: subtext, fontSize: 12, cursor: 'pointer'
            }}>← 返回 / Back</button>
            </div>
          </form>
        }

        <div style={{ marginTop: 24, paddingTop: 20, borderTop: `1px solid ${border}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ color: subtext, fontSize: 11 }}>© 2026 JARVIS Platform v2.4</span>
          <div style={{ display: 'flex', gap: 12 }}>
            <a href="#" style={{ color: subtext, fontSize: 11, textDecoration: 'none' }}>帮助</a>
            <a href="#" style={{ color: subtext, fontSize: 11, textDecoration: 'none' }}>Privacy</a>
          </div>
        </div>
      </div>
    </div>);

};

Object.assign(window, { LoginPage });