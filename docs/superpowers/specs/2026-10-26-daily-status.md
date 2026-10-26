# Daily status for quiet groups

A successful daily run is no longer classified as limited solely because some groups had no archived messages and were skipped. Such a run remains normal and displays `生成成功，N 个群无消息跳过`. Failures, missing report files, limited inputs, unknown inputs and incomplete jobs retain their existing classifications. Skip counts and explanations remain visible.

The overview no longer renders the `仅描述本次任务与本地归档，不能证明 QQ 历史消息无遗漏。` line. Its scope metadata remains in the API to preserve the existing contract. The shared health indicators naturally stop warning when quiet skips are the only issue.

Failure-first regression tests cover all-quiet runs, successful reports mixed with quiet groups, and quiet groups combined with missing/truncated/unknown report inputs. Browser tests check the removed line, normal health for quiet skips, and warnings for genuine limited coverage.

Validation: 201 Python tests passed and 1 skipped; all 61 frontend scripts passed. A rebuilt desktop package reproduced 14 participating groups, 12 readable reports and 2 quiet skips using synthetic data: coverage and global health were normal, the deleted line was absent, and limited coverage still produced a warning. Desktop/mobile layouts had no horizontal overflow and zero page errors. Independent review found no actionable issues.

The desktop backend identity is now `2026-10-26-daily-status` so the updated service is distinguishable from earlier builds. PyInstaller rebuilt the executable; verification matched 119 source modules, 56 templates and 2,192 manifest entries. Evidence is in `D:\Cache\QQDigestDailyStatus-20261026`, with rebuild intermediates in `D:\Cache\QQDigestDesktop`. The release is `D:\Apps\QQDigestDesktop-2026-10-26-DailyStatus-Release`; the prior AppearanceSettings release remains available for rollback.
