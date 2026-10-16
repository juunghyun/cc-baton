import type { EngineInterface, Register, Timer } from 'claude-code'

// 재우기는 SIGTERM 이라 입력창에 쓰다 만 글이 같이 사라진다.
// 쓰는 동안 세션별 파일에 적어 두고, 같은 세션을 resume 하면 입력창에 되채운다.
// 종료 순간이 아니라 편집마다 적는 이유: SIGTERM 뒤 session.end 에선 입력창을 못 읽을 수 있다.

let pending: Timer | undefined
let saved = false // 이 프로세스가 빈 값이 아닌 초안을 남긴 적이 있나 — 없으면 빈 파일을 만들지 않는다

async function draftFile($: EngineInterface) {
  const root = (await $.env.get('XDG_STATE_HOME')) || `${await $.env.get('HOME')}/.local/state`
  return `${root}/cc-baton/drafts/${await $.session.id()}.txt`
}

function keep($: EngineInterface, text: string) {
  pending?.cancel()
  pending = $.clock.after(500, async () => {
    if (!text.trim() && !saved) return
    saved = text.trim() !== ''
    await $.fs.write(await draftFile($), text)
  })
}

async function restore($: EngineInterface, text: string, tries: number) {
  const box = await $.prompt.read()
  if (box.text) return // 그새 사람이 뭔가 쳤다
  const r = await $.prompt.fill({ text })
  if (!r.isFilled && r.refusal !== 'no_composer' && tries > 0)
    $.clock.after(300, () => void restore($, text, tries - 1)) // 입력창이 아직 안 붙었으면 잠깐 뒤 다시
}

export const register: Register = on => {
  on('prompt.edit', async ($, e, next) => {
    const r = await next(e)
    keep($, r.text)
    return r
  })

  on('prompt.change', ($, e, next) => {
    keep($, e.text) // 제출·지우기(clear)면 빈 값 → 파일도 비운다
    return next(e)
  })

  on('session.start', async ($, e, next) => {
    const r = await next(e)
    if (e.isInteractive) {
      const text = await $.fs.read(await draftFile($)).catch(() => '')
      if (text.trim()) void restore($, text, 20)
    }
    return r
  })
}
