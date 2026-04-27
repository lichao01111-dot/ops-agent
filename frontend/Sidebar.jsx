
// Sidebar Component
const Sidebar = ({ theme, agents, currentAgent, onAgentChange, conversations, currentConv, onConvChange, onNewChat, user, onLogout }) => {
  const [search, setSearch] = React.useState('');
  const [collapsed, setCollapsed] = React.useState(false);

  const t = theme || {};
  const bg = t.sidebarBg || '#080f1e';
  const surface = t.surface || '#0d1628';
  const accent = t.accent || '#3b82f6';
  const border = t.border || 'rgba(99,140,210,0.18)';
  const text = t.text || '#e2e8f0';
  const subtext = t.subtext || '#94a3b8';
  const hover = t.hover || 'rgba(99,140,210,0.08)';

  const filteredConvs = conversations.filter(c =>
    c.title.toLowerCase().includes(search.toLowerCase())
  );

  const groupedConvs = React.useMemo(() => {
    const today = [], yesterday = [], older = [];
    filteredConvs.forEach((c, i) => {
      if (i < 3) today.push(c);
      else if (i < 6) yesterday.push(c);
      else older.push(c);
    });
    return { '今天 Today': today, '昨天 Yesterday': yesterday, '更早 Earlier': older };
  }, [filteredConvs]);

  return (
    <div style={{
      width: collapsed ? 60 : 260, minWidth: collapsed ? 60 : 260,
      height: '100%', background: bg, borderRight: `1px solid ${border}`,
      display: 'flex', flexDirection: 'column', transition: 'width 0.2s, min-width 0.2s',
      overflow: 'hidden', position: 'relative', zIndex: 10
    }}>
      {/* Header */}
      <div style={{ padding: collapsed ? '16px 10px' : '16px 16px 12px', borderBottom: `1px solid ${border}` }}>
        {!collapsed && (
          <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom: 14 }}>
            <div style={{
              width: 32, height: 32, borderRadius: 8, flexShrink: 0,
              background: `linear-gradient(135deg, ${accent}, ${accent}99)`,
              display:'flex', alignItems:'center', justifyContent:'center',
              boxShadow: `0 0 12px ${accent}44`
            }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round">
                <path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
              </svg>
            </div>
            <div>
              <div style={{ color: text, fontWeight: 700, fontSize: 15, letterSpacing:'-0.02em' }}>JARVIS</div>
              <div style={{ color: subtext, fontSize: 10, letterSpacing:'0.06em', textTransform:'uppercase' }}>AI Agent Platform</div>
            </div>
            <button onClick={() => setCollapsed(true)} style={{
              marginLeft:'auto', background:'none', border:'none', cursor:'pointer', color: subtext, padding:4, borderRadius:6,
              display:'flex', alignItems:'center', justifyContent:'center'
            }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M15 18l-6-6 6-6"/></svg>
            </button>
          </div>
        )}
        {collapsed && (
          <button onClick={() => setCollapsed(false)} style={{
            background:'none', border:'none', cursor:'pointer', color: subtext, padding:6, borderRadius:6,
            display:'flex', alignItems:'center', justifyContent:'center', width:'100%'
          }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 18l6-6-6-6"/></svg>
          </button>
        )}

        {/* Agent Selector */}
        {!collapsed && (
          <div style={{ marginBottom: 10 }}>
            <div style={{ color: subtext, fontSize: 10, fontWeight: 600, letterSpacing:'0.08em', textTransform:'uppercase', marginBottom: 6 }}>当前 Agent / Active Agent</div>
            <div style={{ display:'flex', flexDirection:'column', gap:4 }}>
              {agents.map(a => (
                <button key={a.id} onClick={() => onAgentChange(a)} style={{
                  display:'flex', alignItems:'center', gap: 10, padding: '8px 10px', borderRadius: 8,
                  background: currentAgent.id === a.id ? `${accent}20` : 'transparent',
                  border: currentAgent.id === a.id ? `1px solid ${accent}44` : '1px solid transparent',
                  cursor:'pointer', textAlign:'left', transition:'all 0.15s', width:'100%'
                }}
                onMouseEnter={e => { if(currentAgent.id !== a.id) e.currentTarget.style.background = hover; }}
                onMouseLeave={e => { if(currentAgent.id !== a.id) e.currentTarget.style.background = 'transparent'; }}
                >
                  <div style={{
                    width: 28, height: 28, borderRadius: 7, background: a.color + '22',
                    border: `1px solid ${a.color}44`, display:'flex', alignItems:'center', justifyContent:'center',
                    fontSize: 14, flexShrink: 0
                  }}>{a.icon}</div>
                  <div style={{ overflow:'hidden' }}>
                    <div style={{ color: currentAgent.id === a.id ? text : subtext, fontSize: 12, fontWeight: 600, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{a.name}</div>
                    <div style={{ color: subtext, fontSize: 10, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis', opacity:0.7 }}>{a.role}</div>
                  </div>
                  {currentAgent.id === a.id && (
                    <div style={{ marginLeft:'auto', width:6, height:6, borderRadius:'50%', background: a.color, flexShrink:0 }}></div>
                  )}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* New Chat */}
        <button onClick={onNewChat} style={{
          width: '100%', background: collapsed ? 'transparent' : `${accent}18`,
          border: collapsed ? 'none' : `1px solid ${accent}30`,
          borderRadius: 8, padding: collapsed ? '8px' : '9px 12px',
          color: accent, fontSize: 12, fontWeight: 600, cursor:'pointer',
          display:'flex', alignItems:'center', justifyContent: collapsed ? 'center' : 'flex-start',
          gap: 8, transition:'all 0.15s'
        }}
        onMouseEnter={e => e.currentTarget.style.background = `${accent}28`}
        onMouseLeave={e => e.currentTarget.style.background = collapsed ? 'transparent' : `${accent}18`}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          {!collapsed && <span>新建对话 / New Chat</span>}
        </button>
      </div>

      {/* Search */}
      {!collapsed && (
        <div style={{ padding: '10px 12px 6px' }}>
          <div style={{ position:'relative' }}>
            <svg style={{ position:'absolute', left:9, top:'50%', transform:'translateY(-50%)', color: subtext }} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索对话 / Search"
              style={{
                width:'100%', background: `rgba(255,255,255,0.04)`, border: `1px solid ${border}`,
                borderRadius: 7, padding:'7px 10px 7px 28px', color: text, fontSize: 12,
                outline:'none', boxSizing:'border-box'
              }}
            />
          </div>
        </div>
      )}

      {/* Conversation List */}
      {!collapsed && (
        <div style={{ flex: 1, overflowY:'auto', padding:'6px 8px' }}>
          {Object.entries(groupedConvs).map(([label, convs]) => convs.length > 0 && (
            <div key={label}>
              <div style={{ color: subtext, fontSize: 10, fontWeight: 600, letterSpacing:'0.07em', textTransform:'uppercase', padding:'8px 6px 2px', opacity:0.5 }}>{label}</div>
              {convs.map(c => (
                <button key={c.id} onClick={() => onConvChange(c)} style={{
                  width:'100%', display:'flex', alignItems:'center', padding:'6px 10px',
                  borderRadius: 7, background: currentConv?.id === c.id ? `${accent}18` : 'transparent',
                  border: 'none', cursor:'pointer', textAlign:'left', transition:'all 0.12s', marginBottom: 1
                }}
                onMouseEnter={e => { if(currentConv?.id !== c.id) e.currentTarget.style.background = hover; }}
                onMouseLeave={e => { if(currentConv?.id !== c.id) e.currentTarget.style.background = 'transparent'; }}
                >
                  <div style={{ color: currentConv?.id === c.id ? text : subtext, fontSize: 12, fontWeight: currentConv?.id === c.id ? 500 : 400, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis', width:'100%' }}>{c.title}</div>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* User Profile */}
      <div style={{ padding: collapsed ? '10px' : '12px 14px', borderTop: `1px solid ${border}`, display:'flex', alignItems:'center', gap:10 }}>
        <div style={{
          width: 32, height: 32, borderRadius: '50%', background: `linear-gradient(135deg, ${accent}88, ${accent}44)`,
          display:'flex', alignItems:'center', justifyContent:'center', color: 'white', fontSize: 13, fontWeight: 700, flexShrink:0
        }}>
          {(user?.display_name || user?.username || 'U').charAt(0).toUpperCase()}
        </div>
        {!collapsed && (
          <>
            <div style={{ flex:1, overflow:'hidden' }}>
              <div style={{ color: text, fontSize: 12, fontWeight: 600, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{user?.display_name || user?.username || 'User'}</div>
              <div style={{ color: subtext, fontSize: 10 }}>{user?.role ? `角色：${user.role}` : 'IT部 · 系统管理员'}</div>
            </div>
            <button
              title="退出登录 / Logout"
              onClick={onLogout}
              style={{ background:'none', border:'none', cursor: onLogout ? 'pointer' : 'default', color: subtext, padding: 4 }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
                <polyline points="16 17 21 12 16 7"/>
                <line x1="21" y1="12" x2="9" y2="12"/>
              </svg>
            </button>
          </>
        )}
      </div>
    </div>
  );
};

Object.assign(window, { Sidebar });
