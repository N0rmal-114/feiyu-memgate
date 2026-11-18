/*
 * Browser half of dsh-feiyu-memgate: the settings page of the plugin's row on the Plugins page.
 *
 * Format. The host serves a package's `./client` export to the page as one script in the client module
 * system's lazy-CJS form: it registers the package name and a factory(require) with the page's module
 * loader, and `require` answers only the page's shared module table (docs/cookbook/adding-a-settings-card.md
 * section 5; packages/client/modules/README.md "What the browser loads" and "Sharing modules"). This file
 * is that script, written by hand: React elements are built with createElement (no JSX), and the only
 * modules it asks for are `react` and `@deepseek-ai/dsh-client-ui-primitives`, both in the shared table.
 * Nothing is compiled, so there is no build step. The one generated part is the field table between the
 * <fields> markers, copied from src/config.js by `npm run sync-client`; tests/client-page.test.js fails
 * when it is stale.
 *
 * Page. The Plugins page renders entries of its keyed slot `plugins.row.config` under the key
 * `<package name>#<row id>` and passes `view` ('summary' | 'page') and `form` ({ state, mutate }) for the
 * row's settings namespace (packages/client/ui-plugin-manager/src/client/slot-contract.ts:160-167, :190-195;
 * PluginManagerPage.tsx:1568-1572, :714). The page follows the host's settings form conventions
 * (packages/client/ui-primitives/src/settings-form): edits are staged, only Save writes, all staged edits
 * go in one `form.mutate(ops, revision)` with the revision they were made against, a changed field shows
 * a tag and a reset that stages a return to the inherited value, and a secret is write-only (an empty
 * draft keeps the stored value). The host primitives are used when this host exports them; otherwise
 * plain elements with the host's style variables stand in.
 *
 * Status. The status block reads GET feiyu-memgate/status (document-relative, like the first-party
 * pages' host routes), which the host half serves to the owner only. It shows no secret, address or
 * memory: secrets arrive as booleans.
 *
 * The host half never depends on this file: without it every setting still works, through /memgate and
 * the profile's patch file.
 */
window.__ModuleLoader__.load({ id: 'dsh-feiyu-memgate', factory: (require) => {
  'use strict'
  const module = { exports: {} }
  const React = require('react')
  let ui = {}
  try {
    ui = require('@deepseek-ai/dsh-client-ui-primitives') || {}
  } catch (error) {
    ui = {}
  }
  const h = React.createElement
  const { useEffect, useRef, useState } = React

  // <fields>
  // Generated from src/config.js by scripts/sync-client.mjs; edit the settings there.
  const FIELDS = [
    {
      "group": "modules",
      "label": {
        "en": "Features",
        "zh": "功能"
      },
      "help": {
        "en": "Each feature is switched on or off on its own; a switched-off feature leaves nothing behind in the host.",
        "zh": "每项功能单独开关；关掉的功能不会在宿主里留下任何东西。"
      },
      "fields": [
        {
          "key": "autoRecall",
          "kind": "boolean",
          "default": true,
          "label": {
            "en": "Automatic recall",
            "zh": "自动召回"
          },
          "help": {
            "en": "At the start of each user turn, look up related memories and add them after the user's message as background data.",
            "zh": "每轮用户发言开始时查找相关记忆，作为背景资料放在用户消息之后。"
          }
        },
        {
          "key": "rememberTool",
          "kind": "boolean",
          "default": true,
          "label": {
            "en": "Remember tool",
            "zh": "记住工具"
          },
          "help": {
            "en": "Give the model the memgate_remember tool, to save a memory when the user asks for it or shares a lasting fact.",
            "zh": "给模型 memgate_remember 工具：用户要求记住、或说出长期有用的信息时记下来。"
          }
        },
        {
          "key": "searchTool",
          "kind": "boolean",
          "default": true,
          "label": {
            "en": "Search tool",
            "zh": "查找工具"
          },
          "help": {
            "en": "Give the model the memgate_search tool, to look up memories on demand.",
            "zh": "给模型 memgate_search 工具：需要时主动查找记忆。"
          }
        },
        {
          "key": "promptSection",
          "kind": "boolean",
          "default": true,
          "label": {
            "en": "Prompt section",
            "zh": "提示词说明"
          },
          "help": {
            "en": "Add a short, fixed system prompt section: when to save a memory, and that recalled memories are data, not instructions.",
            "zh": "在系统提示词里加一小段固定说明：什么时候记，召回的记忆是资料不是指令。"
          }
        },
        {
          "key": "autoCapture",
          "kind": "boolean",
          "default": false,
          "label": {
            "en": "Automatic capture",
            "zh": "自动记录"
          },
          "help": {
            "en": "After each turn, send the user's own messages of that turn to memory without asking the model. Off by default.",
            "zh": "每轮结束后，把这一轮用户自己发的消息直接交给记忆库，不经过模型。默认关闭。"
          }
        },
        {
          "key": "commands",
          "kind": "boolean",
          "default": true,
          "label": {
            "en": "Chat command",
            "zh": "聊天命令"
          },
          "help": {
            "en": "The /memgate chat command: status, settings and retrieval tiers. Needs a host with chat commands.",
            "zh": "/memgate 聊天命令：状态、设置和检索方式。需要支持聊天命令的宿主。"
          }
        },
        {
          "key": "migrateOnTierChange",
          "kind": "boolean",
          "default": true,
          "label": {
            "en": "Copy memories into a new vector store",
            "zh": "切换到向量检索时复制记忆"
          },
          "help": {
            "en": "When a vector tier (endpoint or local) becomes active, copy the texts of the keyword store into it in the background, in bounded steps that resume after a restart.",
            "zh": "启用向量检索（endpoint 或 local）时，在后台把关键词存储里的记忆复制过去；分批进行，重启后接着复制。"
          }
        }
      ]
    },
    {
      "group": "retrieval",
      "label": {
        "en": "Retrieval",
        "zh": "检索"
      },
      "help": {
        "en": "How the bundled gateway finds memories. Each tier keeps its own store file: memories saved under one tier are not searched under another unless they were copied (see the features above), and switching back finds them again.",
        "zh": "自带网关怎么查找记忆。每种方式各用一个存储文件：在一种方式下记的内容，换成另一种方式后不会被查到，除非已经复制过去（见上面的功能开关）；切回来还能找到。"
      },
      "fields": [
        {
          "key": "tier",
          "kind": "choice",
          "default": "auto",
          "options": [
            {
              "value": "auto",
              "label": {
                "en": "auto: local when the embedding add-on is ready, else keyword",
                "zh": "auto：本地向量插件就绪时用 local，否则用 keyword"
              }
            },
            {
              "value": "keyword",
              "label": {
                "en": "keyword: keyword (SQLite, no model)",
                "zh": "keyword：关键词（SQLite，不需要模型）"
              }
            },
            {
              "value": "endpoint",
              "label": {
                "en": "endpoint: embeddings endpoint (OpenAI-compatible)",
                "zh": "endpoint：向量接口（OpenAI 兼容）"
              }
            },
            {
              "value": "local",
              "label": {
                "en": "local: local model (dsh-feiyu-memgate-embed add-on)",
                "zh": "local：本地模型（dsh-feiyu-memgate-embed 插件）"
              }
            }
          ],
          "label": {
            "en": "Retrieval tier",
            "zh": "检索方式"
          },
          "help": {
            "en": "auto uses local while the dsh-feiyu-memgate-embed add-on has a model ready, and keyword otherwise.",
            "zh": "auto：dsh-feiyu-memgate-embed 插件的模型就绪时用 local，否则用 keyword。"
          }
        },
        {
          "key": "hybrid",
          "kind": "boolean",
          "default": false,
          "label": {
            "en": "Combine with keyword search (hybrid)",
            "zh": "同时用关键词检索（混合）"
          },
          "help": {
            "en": "Applies to the vector tiers (endpoint, local, and auto while it resolves to local); under keyword it does nothing. Keyword and vector search are both asked and their results fused. It uses the same vector store file as the tier without it, plus a keyword index file next to it, so switching it on or off loses no memory.",
            "zh": "用于向量检索（endpoint、local，以及 auto 选到 local 的时候）；在 keyword 下不起作用。关键词检索和向量检索同时查，结果合并。它用的向量存储文件和不开它时相同，旁边多一个关键词索引文件，所以开或关都不会丢记忆。"
          }
        },
        {
          "key": "url",
          "kind": "string",
          "default": "",
          "format": "url",
          "label": {
            "en": "Endpoint URL",
            "zh": "向量接口地址"
          },
          "help": {
            "en": "endpoint tier: base URL of an OpenAI-compatible embeddings API, for example http://127.0.0.1:11434/v1.",
            "zh": "endpoint 方式：OpenAI 兼容向量接口的基础地址，例如 http://127.0.0.1:11434/v1。"
          }
        },
        {
          "key": "model",
          "kind": "string",
          "default": "",
          "label": {
            "en": "Endpoint model",
            "zh": "向量模型"
          },
          "help": {
            "en": "endpoint tier: the embeddings model name. A different model starts a new, empty store.",
            "zh": "endpoint 方式：向量模型的名字。换模型会用一个新的空存储。"
          }
        },
        {
          "key": "apiKey",
          "kind": "string",
          "default": "",
          "secret": true,
          "label": {
            "en": "Endpoint API key",
            "zh": "向量接口密钥"
          },
          "help": {
            "en": "endpoint tier: the API key of the embeddings endpoint, if it needs one. It is never shown again after saving.",
            "zh": "endpoint 方式：向量接口的密钥（需要的话）。保存后不再显示。"
          }
        }
      ]
    },
    {
      "group": "gateway",
      "label": {
        "en": "Gateway",
        "zh": "网关"
      },
      "help": {
        "en": "Where memories are kept: the bundled gateway (managed) or a gateway you run yourself (external).",
        "zh": "记忆放在哪里：插件自带的网关（managed），或你自己运行的网关（external）。"
      },
      "fields": [
        {
          "key": "mode",
          "kind": "choice",
          "default": "managed",
          "options": [
            {
              "value": "managed",
              "label": {
                "en": "managed: start the bundled gateway (needs Python 3.11 or newer)",
                "zh": "managed：启动自带的网关（需要 Python 3.11 或更新）"
              }
            },
            {
              "value": "external",
              "label": {
                "en": "external: use a running gateway (see External gateway)",
                "zh": "external：使用已经在运行的网关（见“外部网关”）"
              }
            }
          ],
          "label": {
            "en": "Gateway mode",
            "zh": "网关模式"
          },
          "help": {
            "en": "managed starts the gateway that ships with the plugin; external talks to the address under External gateway.",
            "zh": "managed 启动插件自带的网关；external 连接“外部网关”里填写的地址。"
          }
        },
        {
          "key": "python",
          "kind": "string",
          "default": "",
          "label": {
            "en": "Python interpreter",
            "zh": "Python 解释器"
          },
          "help": {
            "en": "managed: the Python to use (3.11 or newer). Empty: the desktop app's bundled Python, then python3, python and py -3.",
            "zh": "managed：使用哪个 Python（3.11 或更新）。留空：先用桌面版自带的 Python，再找 python3、python、py -3。"
          }
        },
        {
          "key": "dataDir",
          "kind": "string",
          "default": "",
          "label": {
            "en": "Data folder",
            "zh": "数据文件夹"
          },
          "help": {
            "en": "managed: the folder for the memory stores, the generated gateway configuration and its log. Empty: data/feiyu-memgate in the DSH home folder.",
            "zh": "managed：存放记忆库、生成的网关配置和日志的文件夹。留空：DSH 主目录下的 data/feiyu-memgate。"
          }
        }
      ]
    },
    {
      "group": "external",
      "label": {
        "en": "External gateway",
        "zh": "外部网关"
      },
      "help": {
        "en": "Used only in external mode.",
        "zh": "只在 external 模式下使用。"
      },
      "fields": [
        {
          "key": "url",
          "kind": "string",
          "default": "",
          "format": "url",
          "label": {
            "en": "Gateway URL",
            "zh": "网关地址"
          },
          "help": {
            "en": "The gateway address, for example http://127.0.0.1:8080.",
            "zh": "网关地址，例如 http://127.0.0.1:8080。"
          }
        },
        {
          "key": "token",
          "kind": "string",
          "default": "",
          "secret": true,
          "label": {
            "en": "Client token",
            "zh": "客户端令牌"
          },
          "help": {
            "en": "The client token that gateway issued for this client. It is never shown again after saving.",
            "zh": "那个网关给这个客户端的令牌。保存后不再显示。"
          }
        }
      ]
    },
    {
      "group": "advanced",
      "label": {
        "en": "Advanced",
        "zh": "高级"
      },
      "help": {
        "en": "Recall details and the rules of the bundled gateway (the rules apply to managed mode only; an external gateway follows its own configuration).",
        "zh": "召回细节和自带网关的规则（规则只用于 managed 模式；外部网关按它自己的配置）。"
      },
      "fields": [
        {
          "key": "recallLimit",
          "kind": "integer",
          "default": 5,
          "min": 1,
          "max": 10,
          "label": {
            "en": "Memories per turn",
            "zh": "每轮记忆条数"
          },
          "help": {
            "en": "Most memories added per turn (1 to 10); also the default number of memgate_search results.",
            "zh": "每轮最多加入几条记忆（1 到 10），也是 memgate_search 默认返回的条数。"
          }
        },
        {
          "key": "recallTimeoutMs",
          "kind": "integer",
          "default": 4000,
          "min": 500,
          "max": 20000,
          "label": {
            "en": "Recall time limit (ms)",
            "zh": "召回等待上限（毫秒）"
          },
          "help": {
            "en": "How long a turn waits for automatic recall (500 to 20000 ms). A slower lookup is skipped for that turn.",
            "zh": "每轮最多等自动召回多少毫秒（500 到 20000）。超时的话这一轮不加记忆。"
          }
        },
        {
          "key": "language",
          "kind": "choice",
          "default": "auto",
          "options": [
            {
              "value": "auto",
              "label": {
                "en": "auto: follow the system language",
                "zh": "auto：跟随系统语言"
              }
            },
            {
              "value": "zh",
              "label": {
                "en": "zh: Chinese",
                "zh": "zh：中文"
              }
            },
            {
              "value": "en",
              "label": {
                "en": "en: English",
                "zh": "en：英文"
              }
            }
          ],
          "label": {
            "en": "Language",
            "zh": "语言"
          },
          "help": {
            "en": "Language of the memory block, the tool replies and the /memgate command.",
            "zh": "记忆块、工具回复和 /memgate 命令用的语言。"
          }
        },
        {
          "key": "skipRepeats",
          "kind": "boolean",
          "default": true,
          "label": {
            "en": "Skip repeated memories",
            "zh": "不重复给同一条记忆"
          },
          "help": {
            "en": "Leave out a memory that an earlier memory block of this conversation still shows, compared by its text. If nothing new is left, no block is added. Off: only a block identical to the previous one is left out.",
            "zh": "这个对话里前面的记忆块还留着的记忆，不再给第二次（按记忆的文字比较）。没有新的就不加记忆块。关掉：只有和上一个记忆块完全相同时才不加。"
          }
        },
        {
          "key": "linkedRecall",
          "kind": "boolean",
          "default": true,
          "label": {
            "en": "Recall linked memories",
            "zh": "顺带想起相连的记忆"
          },
          "help": {
            "en": "With vector retrieval (the endpoint and local tiers, with or without retrieval.hybrid), after the usual lookup the gateway follows the first two memories it found to the memories closest to them and adds at most two, never beyond advanced.recallLimit. Under keyword retrieval it does nothing.",
            "zh": "用向量检索时（endpoint 和 local 两档，开不开 retrieval.hybrid 都一样），网关在平常的查找之后，顺着找到的前两条记忆去找和它们最接近的记忆，最多再加两条，总数不超过 advanced.recallLimit。用 keyword 检索时什么都不做。"
          }
        },
        {
          "key": "showAge",
          "kind": "boolean",
          "default": true,
          "label": {
            "en": "Show today's date and each memory's age",
            "zh": "写明今天的日期和每条记忆的时间"
          },
          "help": {
            "en": "The memory block says today's date (the date on this computer) and how long ago each dated memory is, for example 3 weeks ago. Off: the block shows only the absolute dates.",
            "zh": "记忆块里写明今天的日期（这台电脑上的日期），以及每条有日期的记忆是多久以前的事，例如“3 周前”。关掉：只写具体日期。"
          }
        },
        {
          "key": "failureNote",
          "kind": "boolean",
          "default": true,
          "label": {
            "en": "Tell the model when recall failed",
            "zh": "召回失败时告诉模型"
          },
          "help": {
            "en": "When the lookup of a turn times out, fails, or the bundled gateway is still starting, add one short note after the user message, so the model does not take it for \"no related memory\". Only on the first turn of a failure streak.",
            "zh": "这一轮的查询超时、出错，或者自带网关还在启动时，在用户消息之后加一行说明，免得模型当作“没有相关记忆”。连续失败时只在第一轮加。"
          }
        },
        {
          "key": "captureMinChars",
          "kind": "integer",
          "default": 4,
          "min": 0,
          "max": 50,
          "label": {
            "en": "Automatic capture: shortest message",
            "zh": "自动记录：最短的消息"
          },
          "help": {
            "en": "With automatic capture on, a message is not saved when it is a command, only filler, or has fewer meaningful characters than this (0 to 50; punctuation, spaces and emoji do not count). 0 turns the length check off.",
            "zh": "自动记录打开时，命令、只有语气词的消息，以及有意义的字数少于这个数的消息不保存（0 到 50；标点、空格和表情不算字数）。0 表示不按字数过滤。"
          }
        },
        {
          "key": "profile",
          "kind": "choice",
          "default": "broad",
          "options": [
            {
              "value": "broad",
              "label": {
                "en": "broad: more memories per turn, misses less, more noise",
                "zh": "broad：每轮条数多，漏得少，杂讯也多"
              }
            },
            {
              "value": "precise",
              "label": {
                "en": "precise: about one memory per turn, closer matches, misses more",
                "zh": "precise：每轮约一条，更贴题，漏得也多"
              }
            }
          ],
          "label": {
            "en": "Recall profile",
            "zh": "召回档位"
          },
          "help": {
            "en": "How strict the gateway is about what counts as related. The memory block stays in the conversation, so broad makes the context grow faster.",
            "zh": "网关判断“相关”的严格程度。记忆块会留在对话里，broad 会让上下文涨得更快。"
          }
        },
        {
          "key": "dailyWrites",
          "kind": "integer",
          "default": 50,
          "min": 0,
          "max": 100000,
          "label": {
            "en": "Daily write cap",
            "zh": "每日写入上限"
          },
          "help": {
            "en": "Most memories the gateway accepts per day; 0 means no cap. The count starts again whenever the gateway restarts.",
            "zh": "网关每天最多收下几条记忆；0 表示不限。网关重启后重新计数。"
          }
        },
        {
          "key": "redaction",
          "kind": "boolean",
          "default": true,
          "label": {
            "en": "Redact secrets",
            "zh": "去掉秘密"
          },
          "help": {
            "en": "Remove secrets such as keys, tokens and passwords from memories when they are saved and when they are recalled.",
            "zh": "保存和召回记忆时去掉密钥、令牌、密码这类秘密。"
          }
        },
        {
          "key": "newerFirst",
          "kind": "boolean",
          "default": true,
          "label": {
            "en": "Newer first",
            "zh": "新的优先"
          },
          "help": {
            "en": "Among nearly equal matches, put newer memories first.",
            "zh": "相关度差不多时，较新的记忆排在前面。"
          }
        },
        {
          "key": "forgetting",
          "kind": "boolean",
          "default": false,
          "label": {
            "en": "Forgetting curve",
            "zh": "遗忘曲线"
          },
          "help": {
            "en": "Memories unused for a long time rank lower and are eventually archived and removed (at most a few per day). Archived memories stay in the data folder for 90 days.",
            "zh": "很久没用到的记忆排得更靠后，最终存档并删除（每天最多几条）。存档的记忆在数据文件夹里保留 90 天。"
          }
        }
      ]
    }
  ]
  // </fields>

  const NS = 'feiyuMemgate'
  const ROW_KEY = 'dsh-feiyu-memgate#feiyu-memgate'
  const STATUS_ROUTE = 'feiyu-memgate/status'
  const STATUS_POLL_MS = 10000
  // The model list: GET reads it, POST is a click (install, cancel, use, remove); src/setup.js.
  const MODELS_ROUTE = 'feiyu-memgate/models'
  const MODELS_POLL_MS = 10000
  const MODELS_BUSY_POLL_MS = 1500
  const INDEX = {}
  for (const group of FIELDS) for (const field of group.fields) INDEX[group.group + '.' + field.key] = Object.assign({ group: group.group }, field)

  const UI = {
    en: {
      summary: 'Long-term memory through the feiyu-memgate gateway: automatic recall, memory tools and retrieval tiers.',
      statusTitle: 'Status',
      refresh: 'Refresh',
      statusLoading: 'Reading the status…',
      statusFailed: 'Status unavailable ({code}).',
      gateway: 'Gateway', python: 'Python', retrieval: 'Retrieval', store: 'Store', embed: 'Embedding add-on',
      migration: 'Copy from the keyword store',
      managed: 'managed', external: 'external',
      'state.idle': 'not started', 'state.starting': 'starting', 'state.running': 'running', 'state.restarting': 'restarting',
      'state.off': 'off', 'state.failed': 'stopped after repeated failures', 'state.stopped': 'stopped',
      'state.unused': 'not running (no memory feature is on)', 'state.configured': 'configured',
      'state.incomplete': 'needs a URL and a token', 'state.ready': 'ready', 'state.loading': 'loading',
      'state.no-model': 'no model installed', 'state.error': 'error', 'state.done': 'done', 'state.partial': 'in progress',
      'state.running-copy': 'copying',
      pythonNone: 'not found (3.11 or newer needed)',
      pythonUnknown: 'not checked',
      retrievalExternal: 'decided by the external gateway',
      hybridWord: 'keyword (hybrid)',
      notInstalled: 'not installed',
      memories: '{count} memories',
      countUnknown: 'count unavailable',
      progress: '{copied} of {total} copied',
      refusedCount: '{count} refused by the embedding model',
      approximate: 'scores not calibrated',
      save: 'Save', saving: 'Saving…', saved: 'Saved.',
      saveFailed: 'The host did not accept the change; nothing was saved.',
      readOnly: 'Settings are read-only in this deployment.',
      unavailable: 'The settings of this plugin are not available right now.',
      loading: 'Loading the settings…',
      overridden: 'Changed', reset: 'Reset',
      invalidInteger: 'A whole number from {min} to {max}.',
      invalidUrl: 'An http:// or https:// address.',
      secretSet: 'Set', secretUnset: 'Not set', secretHint: 'Leave empty to keep the stored value.',
      secretClear: 'Clear', secretClearing: 'Cleared when you save.',
      lang: 'en',
      modelsTitle: 'Local embedding models',
      modelsIntro: 'Optional. With a local model, memory is recalled by meaning rather than by shared words, on this computer. Nothing is downloaded or installed until you press a button.',
      modelsLink: 'Measured retrieval quality of each tier',
      modelsLoading: 'Reading the model list…',
      modelsFailed: 'The model list is unavailable ({code}).',
      colSize: 'Download', colMemory: 'Memory while running', colLanguages: 'Languages', colLicence: 'Licence',
      memoryAbout: 'about {mb} MB', memoryUnknown: 'not measured',
      'language.zh': 'Chinese', 'language.en': 'English', 'language.multilingual': 'Many languages',
      unverified: 'Not yet tested',
      actInstall: 'Download and enable', actDownloading: 'Downloading {percent}%', actDownloadingPlain: 'Downloading…',
      actCancel: 'Cancel', actUse: 'Enable', actActive: 'In use', actLoading: 'Loading…', actRemove: 'Delete',
      acceptLabel: 'I have read and accept the {name}',
      confirmAddon: 'This installs the plugin dsh-feiyu-memgate-embed from npm through the DSH plugin manager (it checks that the plugin supports this DSH first), then downloads {model} ({size}) and enables it. The plugin adds a dependency with a native binary (node-llama-cpp): about 90 MB on disk for the CPU build, up to about 740 MB on Windows and Linux x64, where pnpm also fetches the GPU builds. The model runs on the CPU only. On Windows the engine needs the Microsoft Visual C++ 2015-2022 runtime (x64), which most Windows installations already have.',
      confirmYes: 'Install', confirmNo: 'Cancel',
      declined: 'Nothing was installed. Press the button again when you want it.',
      todo: 'What to do:',
      'phase.addon': 'Installing the embedding add-on…',
      'phase.addon-starting': 'The embedding add-on is installed; waiting for it to start…',
      'phase.model': 'Downloading {model}…',
      'phase.modelPercent': 'Downloading {model}: {percent}%',
      'phase.activating': 'Enabling {model}…',
      'phase.done': '{model} is in use; the retrieval tier follows it (see the status above).',
      'installState.installing': 'The host is installing it',
      'installState.applying': 'The host is starting it',
      'installState.cancelling': 'Cancelling',
      actResume: 'Resume download',
      'phase.paused': 'The download of {model} was interrupted (DSH restarted); the part already downloaded is kept.',
      'phase.builds': 'The installation waits for your decision:',
      buildsAllow: 'Allow the script and continue', buildsDeny: 'Do not allow',
      pinned: 'Pinned in the add-on’s settings',
    },
    zh: {
      summary: '通过 feiyu-memgate 网关使用长期记忆：自动召回、记忆工具和多种检索方式。',
      statusTitle: '状态',
      refresh: '刷新',
      statusLoading: '正在读取状态…',
      statusFailed: '状态不可用（{code}）。',
      gateway: '网关', python: 'Python', retrieval: '检索', store: '存储', embed: '向量插件',
      migration: '从关键词存储复制',
      managed: '自带', external: '外部',
      'state.idle': '未启动', 'state.starting': '启动中', 'state.running': '运行中', 'state.restarting': '重启中',
      'state.off': '关闭', 'state.failed': '多次失败后已停止', 'state.stopped': '已停止',
      'state.unused': '没有运行（没有开启需要记忆的功能）', 'state.configured': '已配置',
      'state.incomplete': '需要地址和令牌', 'state.ready': '就绪', 'state.loading': '加载中',
      'state.no-model': '没有安装模型', 'state.error': '出错', 'state.done': '完成', 'state.partial': '进行中',
      'state.running-copy': '复制中',
      pythonNone: '没有找到（需要 3.11 或更新）',
      pythonUnknown: '未检查',
      retrievalExternal: '由外部网关决定',
      hybridWord: '关键词（混合）',
      notInstalled: '未安装',
      memories: '{count} 条记忆',
      countUnknown: '条数未知',
      progress: '已复制 {copied} / {total}',
      refusedCount: '向量模型拒收 {count} 条',
      approximate: '分数未校准',
      save: '保存', saving: '正在保存…', saved: '已保存。',
      saveFailed: '宿主没有接受这次修改，什么都没有保存。',
      readOnly: '这个部署的设置是只读的。',
      unavailable: '这个插件的设置现在不可用。',
      loading: '正在加载设置…',
      overridden: '已修改', reset: '恢复',
      invalidInteger: '{min} 到 {max} 之间的整数。',
      invalidUrl: 'http:// 或 https:// 开头的地址。',
      secretSet: '已设置', secretUnset: '未设置', secretHint: '留空则保留已保存的值。',
      secretClear: '清空', secretClearing: '保存时清空。',
      lang: 'zh',
      modelsTitle: '本地向量模型',
      modelsIntro: '可选。有了本地模型，记忆按意思而不是按字面找回，全部在这台电脑上完成。按下按钮之前不会下载或安装任何东西。',
      modelsLink: '各档位的实测检索效果',
      modelsLoading: '正在读取模型列表…',
      modelsFailed: '模型列表不可用（{code}）。',
      colSize: '下载', colMemory: '运行内存', colLanguages: '语言', colLicence: '许可',
      memoryAbout: '约 {mb} MB', memoryUnknown: '未测',
      'language.zh': '中文', 'language.en': '英文', 'language.multilingual': '多语言',
      unverified: '尚未测试',
      actInstall: '下载并启用', actDownloading: '下载中 {percent}%', actDownloadingPlain: '下载中…',
      actCancel: '取消', actUse: '启用', actActive: '使用中', actLoading: '加载中…', actRemove: '删除',
      acceptLabel: '我已阅读并同意 {name}',
      confirmAddon: '这会通过 DSH 的插件管理器从 npm 安装插件 dsh-feiyu-memgate-embed（先检查它是否支持当前 DSH），然后下载 {model}（{size}）并启用。这个插件会带来一个含原生二进制的依赖（node-llama-cpp）：CPU 版约占 90 MB 磁盘；在 Windows 和 Linux x64 上 pnpm 还会取 GPU 版本，最多约 740 MB。模型只在 CPU 上运行。在 Windows 上，引擎需要 Microsoft Visual C++ 2015-2022 运行库（x64），大多数 Windows 已经装有。',
      confirmYes: '安装', confirmNo: '取消',
      declined: '没有安装任何东西。想装的时候再按一次按钮。',
      todo: '怎么办：',
      'phase.addon': '正在安装向量插件…',
      'phase.addon-starting': '向量插件已安装，等待它启动…',
      'phase.model': '正在下载 {model}…',
      'phase.modelPercent': '正在下载 {model}：{percent}%',
      'phase.activating': '正在启用 {model}…',
      'phase.done': '{model} 正在使用，检索方式随之切换（见上面的状态）。',
      'installState.installing': '宿主正在安装',
      'installState.applying': '宿主正在启动它',
      'installState.cancelling': '正在取消',
      actResume: '继续下载',
      'phase.paused': '{model} 的下载中断了（DSH 重启过），已下载的部分还在。',
      'phase.builds': '安装在等你决定：',
      buildsAllow: '允许脚本并继续', buildsDeny: '不允许',
      pinned: '向量插件的设置里指定了它',
    },
  }

  /** The page's dictionaries: the static copy plus every group, field and option of FIELDS, per language. */
  function dictionaries() {
    const out = {}
    for (const lang of ['en', 'zh']) {
      const dict = Object.assign({}, UI[lang])
      for (const group of FIELDS) {
        dict['g.' + group.group] = group.label[lang]
        dict['gh.' + group.group] = group.help[lang]
        for (const field of group.fields) {
          const k = group.group + '.' + field.key
          dict['f.' + k] = field.label[lang]
          dict['h.' + k] = field.help[lang]
          for (const option of field.options || []) dict['o.' + k + '.' + option.value] = option.label[lang]
        }
      }
      out[lang] = dict
    }
    return out
  }

  // ------------------------------------------------------------ form logic (pure)

  function at(value, path) {
    let node = value
    for (const key of path) {
      if (node === null || typeof node !== 'object' || !Object.prototype.hasOwnProperty.call(node, key)) return undefined
      node = node[key]
    }
    return node
  }

  /** Whether a staged text is a value the field accepts (the host validates again on save). */
  function checkText(field, text) {
    const raw = String(text).trim()
    if (field.kind === 'integer') {
      if (!/^-?\d{1,9}$/.test(raw)) return false
      const n = Number(raw)
      return n >= field.min && n <= field.max
    }
    if (field.kind === 'string' && field.format === 'url' && raw) return /^https?:\/\/[^\s/]+\S*$/i.test(raw)
    return true
  }

  /** What one field shows: its current or staged value, whether it is changed, invalid or being cleared. */
  function fieldView(field, groupKey, state, draft, secrets) {
    const k = groupKey + '.' + field.key
    const staged = draft[k]
    const path = [groupKey, field.key]
    if (field.secret) {
      return {
        k, field, secret: true,
        text: staged && !staged.unset ? staged.value : '',
        clearing: Boolean(staged && staged.unset),
        configured: secrets && typeof secrets[k] === 'boolean' ? secrets[k] : null,
        invalid: false, overridden: false,
      }
    }
    const inherited = at(state.base, path) !== undefined ? at(state.base, path) : field.default
    const stored = at(state.value, path) !== undefined ? at(state.value, path) : field.default
    const value = staged ? (staged.unset ? inherited : staged.value) : stored
    const text = field.kind === 'integer' || field.kind === 'string' ? String(value) : value
    return {
      k, field, secret: false, value, text,
      overridden: staged ? !staged.unset : at(state.user, path) !== undefined,
      invalid: (field.kind === 'integer' || field.kind === 'string') && !checkText(field, text),
    }
  }

  /** The staged edits as Settings path operations, in field order. */
  function buildOps(draft) {
    const ops = []
    for (const k of Object.keys(INDEX)) {
      const staged = draft[k]
      if (!staged) continue
      const field = INDEX[k]
      const path = [field.group, field.key]
      if (staged.unset) {
        ops.push({ op: 'unset', path })
        continue
      }
      if (field.secret && staged.value === '') continue // an empty secret draft keeps the stored value
      let value = staged.value
      if (field.kind === 'integer') value = Number(String(value).trim())
      else if (field.kind === 'string') value = String(value).trim()
      ops.push({ op: 'set', path, value })
    }
    return ops
  }

  /** Write every staged edit in one mutation, fenced by the revision the edits were made against. */
  async function saveDraft(form, draft) {
    const ops = buildOps(draft)
    if (!ops.length) return true
    return (await form.mutate(ops, form.state.revision)) === true
  }

  function stateText(t, state) {
    const key = 'state.' + state
    const text = t(key)
    return text && text !== key ? text : String(state)
  }

  /** The status block as [label, text] rows. */
  function statusRows(data, t) {
    const rows = []
    const g = data.gateway || {}
    const managed = g.mode !== 'external'
    rows.push([t('gateway'), (managed ? t('managed') : t('external')) + ' · ' + stateText(t, g.state) + (!managed && g.url ? ' · ' + g.url : '')])
    if (managed) {
      const p = data.python
      rows.push([t('python'), !p ? t('pythonUnknown') : p.found ? p.version + ' (' + p.source + ') · ' + p.executable : t('pythonNone')])
      const r = data.retrieval || {}
      rows.push([t('retrieval'), r.setting + (r.active ? ' → ' + r.active + (r.hybrid && r.hybrid.active ? ' + ' + t('hybridWord') : '') : '')])
      if (data.store) {
        const count = data.store.count === null || data.store.count === undefined ? t('countUnknown') : t('memories', { count: data.store.count })
        rows.push([t('store'), data.store.type + ' · ' + data.store.file + (data.store.keywordFile ? ' + ' + data.store.keywordFile : '') + ' · ' + count])
      }
    } else {
      rows.push([t('retrieval'), t('retrievalExternal')])
    }
    const e = data.embed
    if (!e) rows.push([t('embed'), t('notInstalled')])
    else {
      const parts = [stateText(t, e.state)]
      if (e.error) parts.push(e.error)
      if (e.model) parts.push(e.model)
      if (e.dimensions) parts.push(e.dimensions + 'd')
      if (e.parity === 'approximate') parts.push(t('approximate'))
      rows.push([t('embed'), parts.join(' · ')])
    }
    const m = data.migration
    if (m) {
      const state = m.state === 'running' ? 'running-copy' : m.state
      const refused = m.refused > 0 ? ' · ' + t('refusedCount', { count: m.refused }) : ''
      rows.push([t('migration'), stateText(t, state) + ' · ' + t('progress', { copied: m.copied || 0, total: m.total || 0 }) + refused])
    }
    return rows
  }

  async function fetchStatus(fetchImpl) {
    const response = await fetchImpl(STATUS_ROUTE, { headers: { accept: 'application/json' }, credentials: 'same-origin' })
    if (!response.ok) {
      const error = new Error('status')
      error.code = 'HTTP ' + response.status
      throw error
    }
    return response.json()
  }

  async function fetchModels(fetchImpl) {
    const response = await fetchImpl(MODELS_ROUTE, { headers: { accept: 'application/json' }, credentials: 'same-origin' })
    if (!response.ok) {
      const error = new Error('models')
      error.code = 'HTTP ' + response.status
      throw error
    }
    return response.json()
  }

  /** One click, sent to the host half: { result: { ok, code, problem }, view }. */
  async function postModels(fetchImpl, body) {
    const response = await fetchImpl(MODELS_ROUTE, {
      method: 'POST',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      const error = new Error('models')
      error.code = 'HTTP ' + response.status
      throw error
    }
    return response.json()
  }

  /** A { en, zh } text from the host half in the page's language. */
  function pickText(text, lang) {
    if (!text || typeof text !== 'object') return ''
    return text[lang] || text.en || ''
  }

  function formatSize(bytes) {
    if (typeof bytes !== 'number' || !(bytes > 0)) return '?'
    return bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + ' GB' : (bytes / 1e6).toFixed(1) + ' MB'
  }

  // The add-on's measurement { loaded, peak } (MB) as "about N MB": the loaded figure, the memory the
  // model process holds while it serves; "not measured" when there is none.
  function memoryText(memoryMB, t) {
    const mb = memoryMB && typeof memoryMB === 'object' ? (memoryMB.loaded > 0 ? memoryMB.loaded : memoryMB.peak > 0 ? memoryMB.peak : null) : null
    return mb ? t('memoryAbout', { mb: String(Math.round(mb)) }) : t('memoryUnknown')
  }

  /**
   * What a model row offers: the primary button (label, action, whether it can be pressed) and a
   * secondary one (cancel while downloading, delete when downloaded and not in use).
   */
  function modelActions(model, view, accepted) {
    const otherBusy = view.busy && view.chain.target !== model.id
    const needsTick = Boolean(model.requiresAcceptance) && model.state === 'absent' && !accepted
    switch (model.state) {
      case 'downloading':
        return {
          primary: { label: model.percent === null || model.percent === undefined ? ['actDownloadingPlain'] : ['actDownloading', { percent: model.percent }], action: null, enabled: false },
          secondary: { label: ['actCancel'], action: 'cancel' },
        }
      case 'installed':
        return { primary: { label: ['actUse'], action: 'use', enabled: !otherBusy }, secondary: { label: ['actRemove'], action: 'remove' } }
      case 'active':
        return { primary: { label: ['actActive'], action: null, enabled: false }, secondary: null }
      case 'loading':
        return { primary: { label: ['actLoading'], action: null, enabled: false }, secondary: null }
      case 'paused':
        return { primary: { label: ['actResume'], action: 'install', enabled: !otherBusy }, secondary: { label: ['actCancel'], action: 'cancel' } }
      default: {
        const mine = view.busy && view.chain.target === model.id
        return {
          primary: { label: ['actInstall'], action: 'install', enabled: !otherBusy && !mine && !needsTick },
          secondary: mine && view.chain.phase === 'addon' ? { label: ['actCancel'], action: 'cancel' } : null,
        }
      }
    }
  }

  /** The line on where the setup stands, or null. */
  function chainLine(view, t) {
    const c = view.chain
    if (!c || !c.target) return null
    const model = c.target
    switch (c.phase) {
      case 'addon': {
        const s = c.installState && c.installState.phase ? ' · ' + t('installState.' + c.installState.phase) : ''
        return t('phase.addon') + s
      }
      case 'addon-starting':
        return t('phase.addon-starting')
      case 'paused':
        return t('phase.paused', { model })
      case 'model':
        return c.percent === null || c.percent === undefined ? t('phase.model', { model }) : t('phase.modelPercent', { model, percent: c.percent })
      case 'activating':
        return t('phase.activating', { model })
      case 'done':
        return t('phase.done', { model })
      default:
        return null
    }
  }

  // ------------------------------------------------------------ view

  const S = {
    page: { display: 'flex', flexDirection: 'column', gap: '16px' },
    status: { padding: '12px', border: '0.5px solid var(--dsw-alias-border-l2)', borderRadius: 'var(--dsw-radius-md)', background: 'var(--dsw-alias-bg-layer-2)' },
    statusHead: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', marginBottom: '8px' },
    title: { margin: 0, fontSize: '13px', fontWeight: 600, lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)' },
    rows: { display: 'grid', gridTemplateColumns: 'max-content 1fr', columnGap: '12px', rowGap: '4px', margin: 0, fontSize: '12px', lineHeight: 1.5 },
    dt: { color: 'var(--dsw-alias-label-secondary)' },
    dd: { margin: 0, minWidth: 0, color: 'var(--dsw-alias-label-primary)', overflowWrap: 'anywhere' },
    section: { display: 'flex', flexDirection: 'column' },
    sectionTitle: { margin: '0 0 4px', fontSize: '14px', fontWeight: 600, lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)' },
    hint: { margin: 0, fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-label-tertiary)' },
    note: { margin: 0, fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-label-secondary)' },
    field: { display: 'flex', flexDirection: 'column', gap: '6px', padding: '12px 0', borderTop: '0.5px solid var(--dsw-alias-border-l2)' },
    head: { display: 'flex', alignItems: 'center', gap: '8px' },
    label: { flex: 1, minWidth: 0, fontSize: '13px', fontWeight: 500, lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)' },
    badges: { display: 'inline-flex', alignItems: 'center', gap: '8px' },
    tag: { padding: '0 6px', borderRadius: 'var(--dsw-radius-sm)', fontSize: '11px', lineHeight: '18px', background: 'var(--dsw-alias-bg-layer-4)', color: 'var(--dsw-alias-label-secondary)' },
    reset: { border: 'none', background: 'none', padding: 0, font: 'inherit', fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-label-secondary)', cursor: 'pointer' },
    input: { height: '34px', padding: '0 12px', border: '0.5px solid var(--dsw-alias-border-l4)', borderRadius: 'var(--dsw-radius-md)', background: 'var(--dsw-alias-bg-layer-3)', font: 'inherit', fontSize: '13px', lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)' },
    invalid: { margin: 0, fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-state-error-primary)' },
    footer: { display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '12px', paddingTop: '12px' },
    button: { height: '32px', padding: '0 14px', border: '0.5px solid var(--dsw-alias-border-l4)', borderRadius: 'var(--dsw-radius-md)', background: 'var(--dsw-alias-bg-layer-3)', font: 'inherit', fontSize: '13px', color: 'var(--dsw-alias-label-primary)', cursor: 'pointer' },
    models: { display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' },
    model: { display: 'flex', flexDirection: 'column', gap: '6px', padding: '12px', border: '0.5px solid var(--dsw-alias-border-l2)', borderRadius: 'var(--dsw-radius-md)' },
    modelHead: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px' },
    modelName: { fontSize: '13px', fontWeight: 600, lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)', overflowWrap: 'anywhere' },
    facts: { display: 'flex', flexWrap: 'wrap', columnGap: '16px', rowGap: '2px', margin: 0, fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-label-secondary)' },
    actions: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px' },
    check: { display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)' },
    problem: { margin: 0, padding: '8px 12px', borderRadius: 'var(--dsw-radius-md)', fontSize: '12px', lineHeight: 1.5, background: 'var(--dsw-alias-bg-layer-4)', color: 'var(--dsw-alias-label-primary)' },
    confirm: { display: 'flex', flexDirection: 'column', gap: '8px', padding: '8px 12px', borderRadius: 'var(--dsw-radius-md)', background: 'var(--dsw-alias-bg-layer-4)' },
    link: { fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-label-link, var(--dsw-alias-label-secondary))' },
  }

  function Tag(props) {
    return ui.Tag ? h(ui.Tag, { tone: props.tone || 'neutral' }, props.children) : h('span', { style: S.tag }, props.children)
  }

  function SmallButton(props) {
    if (ui.Button) return h(ui.Button, { variant: 'outline', size: 'sm', type: 'button', disabled: props.disabled, onClick: props.onClick }, props.children)
    return h('button', { type: 'button', style: S.button, disabled: props.disabled, onClick: props.onClick }, props.children)
  }

  /** Label, change tag and reset, control, then the hint or the reason the draft is refused. */
  function FieldFrame(props) {
    const { t, view, disabled } = props
    return h('div', { style: S.field },
      h('div', { style: S.head },
        h('label', { style: S.label, htmlFor: props.id }, t('f.' + view.k)),
        view.overridden
          ? h('span', { style: S.badges }, h(Tag, null, t('overridden')),
            h('button', { type: 'button', style: S.reset, disabled, onClick: props.onReset }, t('reset')))
          : null,
        props.inline || null),
      props.control || null,
      view.invalid ? h('p', { style: S.invalid }, props.invalidText) : h('p', { style: S.hint }, props.hint !== undefined ? props.hint : t('h.' + view.k)))
  }

  function invalidText(t, field) {
    return field.kind === 'integer' ? t('invalidInteger', { min: field.min, max: field.max }) : t('invalidUrl')
  }

  function Field(props) {
    const { t, view, disabled, edit, reset } = props
    const field = view.field
    const id = 'feiyu-memgate-' + view.k.replace('.', '-')
    if (view.secret) {
      const stateLabel = view.configured === null ? '' : view.configured ? t('secretSet') : t('secretUnset')
      const clear = h('button', { type: 'button', style: S.reset, disabled: disabled || view.clearing, onClick: () => reset(view.k) }, t('secretClear'))
      const hint = view.clearing ? t('secretClearing') : t('h.' + view.k) + ' ' + t('secretHint')
      if (ui.SettingsSecretField) {
        return h('div', null,
          h(ui.SettingsSecretField, { id, label: t('f.' + view.k), hint, text: view.text, disabled, configured: view.configured === true, stateLabel, onEdit: (text) => edit(view.k, text) }),
          h('div', { style: S.badges }, clear))
      }
      return h(FieldFrame, {
        t, view, id, disabled, hint,
        inline: h('span', { style: S.badges }, stateLabel ? h(Tag, null, stateLabel) : null, clear),
        control: h('input', { id, type: 'password', autoComplete: 'new-password', style: S.input, value: view.text, disabled, onChange: (event) => edit(view.k, event.target.value) }),
      })
    }
    if (field.kind === 'boolean') {
      const label = t('f.' + view.k)
      const control = ui.Switch
        ? h(ui.Switch, { checked: view.value === true, label, disabled, onChange: (next) => edit(view.k, next) })
        : h('input', { type: 'checkbox', id, checked: view.value === true, 'aria-label': label, disabled, onChange: (event) => edit(view.k, event.target.checked) })
      return h(FieldFrame, { t, view, id, disabled, inline: control, onReset: () => reset(view.k) })
    }
    if (field.kind === 'choice') {
      const control = h('select', { id, style: S.input, value: view.value, disabled, onChange: (event) => edit(view.k, event.target.value) },
        field.options.map((option) => h('option', { key: option.value, value: option.value }, t('o.' + view.k + '.' + option.value))))
      return h(FieldFrame, { t, view, id, disabled, control, onReset: () => reset(view.k) })
    }
    if (ui.SettingsValueField) {
      return h(ui.SettingsValueField, {
        id, label: t('f.' + view.k), hint: t('h.' + view.k), text: view.text, overridden: view.overridden, invalid: view.invalid,
        overriddenLabel: t('overridden'), resetLabel: t('reset'), invalidLabel: invalidText(t, field), disabled,
        numeric: field.kind === 'integer', onEdit: (text) => edit(view.k, text), onReset: () => reset(view.k),
      })
    }
    const control = h('input', {
      id, type: 'text', style: S.input, value: view.text, disabled, 'aria-invalid': view.invalid || undefined,
      inputMode: field.kind === 'integer' ? 'numeric' : undefined, onChange: (event) => edit(view.k, event.target.value),
    })
    return h(FieldFrame, { t, view, id, disabled, control, invalidText: invalidText(t, field), onReset: () => reset(view.k) })
  }

  function useStatus(fetchImpl) {
    const [state, setState] = useState({ loading: true, data: null, error: null })
    const alive = useRef(true)
    const load = useRef(null)
    load.current = () => fetchStatus(fetchImpl).then(
      (data) => { if (alive.current) setState({ loading: false, data, error: null }) },
      (error) => { if (alive.current) setState((previous) => ({ loading: false, data: previous.data, error: (error && error.code) || 'network' })) },
    )
    useEffect(() => {
      alive.current = true
      load.current()
      const timer = setInterval(() => load.current(), STATUS_POLL_MS)
      return () => {
        alive.current = false
        clearInterval(timer)
      }
    }, [])
    return Object.assign({}, state, {
      refresh: () => load.current(),
      // The gateway restarts after most changes: read again once it has had a moment.
      refreshSoon: () => setTimeout(() => { if (alive.current) load.current() }, 1500),
    })
  }

  function StatusBlock(props) {
    const { t, status } = props
    let body
    if (status.data) {
      body = h('dl', { style: S.rows }, statusRows(status.data, t).flatMap((row, index) => [
        h('dt', { key: 'dt' + index, style: S.dt }, row[0]),
        h('dd', { key: 'dd' + index, style: S.dd }, row[1]),
      ]))
    } else {
      body = h('p', { style: S.note, role: 'status' }, status.loading ? t('statusLoading') : t('statusFailed', { code: status.error }))
    }
    return h('section', { style: S.status, 'aria-label': t('statusTitle') },
      h('div', { style: S.statusHead },
        h('h4', { style: S.title }, t('statusTitle')),
        h(SmallButton, { onClick: status.refresh }, t('refresh'))),
      status.data && status.error ? h('p', { style: S.note, role: 'status' }, t('statusFailed', { code: status.error })) : null,
      body)
  }

  // The answer to a click and the stored state of the chain often carry the same failure (a declined
  // install script is both). Found in a real host: the message was shown twice. Show the stored one.
  function sameProblem(a, b) {
    return Boolean(a && b && a.code && a.code === b.code)
  }

  function Problem(props) {
    const { problem, lang, t } = props
    if (!problem) return null
    return h('p', { style: S.problem, role: 'alert' },
      pickText(problem.title, lang), ' ', h('strong', null, t('todo')), ' ', pickText(problem.todo, lang))
  }

  function ModelRow(props) {
    const { t, lang, model, view, accepted, onAccept, onAction, pending } = props
    const acts = modelActions(model, view, accepted)
    const languages = (model.languages || []).map((l) => t('language.' + l) === 'language.' + l ? l : t('language.' + l)).join(lang === 'zh' ? '、' : ', ')
    // The memory the add-on measured for the running model process (loaded), never the file size.
    const memory = memoryText(model.memoryMB, t)
    const licence = model.license
      ? (model.license.url ? h('a', { href: model.license.url, target: '_blank', rel: 'noreferrer noopener', style: S.link }, model.license.name) : model.license.name)
      : '?'
    const id = 'feiyu-memgate-accept-' + model.id
    const press = (action) => () => { if (action) onAction(action, model) }
    return h('li', { style: S.model },
      h('div', { style: S.modelHead },
        h('span', { style: S.modelName }, model.id),
        model.tierLabel ? h(Tag, null, pickText(model.tierLabel, lang)) : null,
        model.status === 'unverified' ? h(Tag, { tone: 'warning' }, t('unverified')) : null,
        model.pinned ? h(Tag, null, t('pinned')) : null),
      model.note ? h('p', { style: S.note }, pickText(model.note, lang)) : null,
      h('p', { style: S.facts },
        h('span', null, t('colSize') + ' ' + formatSize(model.sizeBytes)),
        h('span', null, t('colMemory') + ' ' + memory),
        h('span', null, t('colLanguages') + ' ' + (languages || '?')),
        h('span', null, t('colLicence') + ' ', licence)),
      model.requiresAcceptance && model.state === 'absent'
        ? h('label', { style: S.check, htmlFor: id },
          h('input', { id, type: 'checkbox', checked: accepted, onChange: (event) => onAccept(model.id, event.target.checked) }),
          t('acceptLabel', { name: model.requiresAcceptance.name }), ' ',
          model.requiresAcceptance.url ? h('a', { href: model.requiresAcceptance.url, target: '_blank', rel: 'noreferrer noopener', style: S.link }, model.requiresAcceptance.url) : null)
        : null,
      h('div', { style: S.actions },
        h(SmallButton, { disabled: pending || !acts.primary.enabled, onClick: press(acts.primary.action) }, t(acts.primary.label[0], acts.primary.label[1])),
        acts.secondary ? h(SmallButton, { disabled: pending, onClick: press(acts.secondary.action) }, t(acts.secondary.label[0])) : null))
  }

  /** The local embedding models: what each costs, its state and its one button. */
  function ModelsSection(props) {
    const { t, onChange } = props
    const fetchImpl = props.fetch
    const lang = t('lang') === 'zh' ? 'zh' : 'en'
    const [state, setState] = useState({ view: null, error: null })
    const [accepted, setAccepted] = useState({})
    const [confirm, setConfirm] = useState(null) // the model waiting for the add-on confirmation
    const [declined, setDeclined] = useState(false)
    const [pending, setPending] = useState(false)
    const [refusal, setRefusal] = useState(null)
    const alive = useRef(true)
    const load = useRef(null)
    const lastPhase = useRef(null)
    load.current = () => fetchModels(fetchImpl).then(
      (view) => { if (alive.current) setState({ view, error: null }) },
      (error) => { if (alive.current) setState((previous) => ({ view: previous.view, error: (error && error.code) || 'network' })) },
    )
    const busy = Boolean(state.view && state.view.busy)
    useEffect(() => {
      alive.current = true
      load.current()
      return () => { alive.current = false }
    }, [])
    useEffect(() => {
      const timer = setInterval(() => load.current(), busy ? MODELS_BUSY_POLL_MS : MODELS_POLL_MS)
      return () => clearInterval(timer)
    }, [busy])
    const phase = state.view && state.view.chain ? state.view.chain.phase : null
    useEffect(() => {
      // The tier and the copy follow a model that became active: read the status again.
      if (phase === 'done' && lastPhase.current && lastPhase.current !== 'done' && onChange) onChange()
      lastPhase.current = phase
    }, [phase])

    const send = (body) => {
      setPending(true)
      setRefusal(null)
      postModels(fetchImpl, body).then((answer) => {
        if (!alive.current) return
        setPending(false)
        if (answer && answer.view) setState({ view: answer.view, error: null })
        if (answer && answer.result && !answer.result.ok && answer.result.problem) setRefusal(answer.result.problem)
      }, (error) => {
        if (!alive.current) return
        setPending(false)
        setState((previous) => ({ view: previous.view, error: (error && error.code) || 'network' }))
      })
    }
    const installBody = (model) => {
      // `confirmed`: the person pressed the button that names the download (and, when the add-on is
      // missing, confirmed what it installs); the host route refuses an install without it.
      const body = { action: 'install', id: model.id, confirmed: true }
      if (model.requiresAcceptance) body.acceptLicence = model.requiresAcceptance.name
      return body
    }
    const onAction = (action, model) => {
      setDeclined(false)
      if (action === 'install' && state.view.addon.state === 'absent') {
        setConfirm(model.id) // the add-on comes first: say what will be installed, then wait for a yes
        return
      }
      if (action === 'install') return send(installBody(model))
      const body = { action }
      if (action !== 'cancel') body.id = model.id
      send(body)
    }

    const view = state.view
    let body
    if (!view) {
      body = h('p', { style: S.note, role: 'status' }, state.error ? t('modelsFailed', { code: state.error }) : t('modelsLoading'))
    } else {
      const waiting = confirm ? view.models.find((m) => m.id === confirm) : null
      const line = chainLine(view, t)
      body = h('div', null,
        state.error ? h('p', { style: S.note, role: 'status' }, t('modelsFailed', { code: state.error })) : null,
        view.python && view.python.problem ? h(Problem, { problem: view.python.problem, lang, t }) : null,
        line ? h('p', { style: S.note, role: 'status' }, line) : null,
        view.chain && view.chain.error ? h(Problem, { problem: view.chain.error, lang, t }) : null,
        refusal && !sameProblem(refusal, view.chain && view.chain.error) ? h(Problem, { problem: refusal, lang, t }) : null,
        declined ? h('p', { style: S.note, role: 'status' }, t('declined')) : null,
        view.builds
          ? h('div', { style: S.confirm, role: 'dialog', 'aria-label': t('phase.builds') },
            h('p', { style: S.note }, h('strong', null, t('phase.builds')), ' ', pickText(view.builds.note, lang)),
            h('div', { style: S.actions },
              h(SmallButton, { disabled: pending, onClick: () => send({ action: 'builds', allow: true, confirmed: true }) }, t('buildsAllow')),
              h(SmallButton, { disabled: pending, onClick: () => send({ action: 'builds', allow: false, confirmed: true }) }, t('buildsDeny'))))
          : null,
        waiting
          ? h('div', { style: S.confirm, role: 'dialog', 'aria-label': t('modelsTitle') },
            h('p', { style: S.note }, view.confirm
              ? pickText(view.confirm, lang).replace('{model}', waiting.id).replace('{size}', formatSize(waiting.sizeBytes))
              : t('confirmAddon', { model: waiting.id, size: formatSize(waiting.sizeBytes) })),
            h('div', { style: S.actions },
              h(SmallButton, { disabled: pending, onClick: () => { setConfirm(null); send(installBody(waiting)) } }, t('confirmYes')),
              h(SmallButton, { disabled: pending, onClick: () => { setConfirm(null); setDeclined(true) } }, t('confirmNo'))))
          : null,
        h('ul', { style: Object.assign({ listStyle: 'none', padding: 0, margin: 0 }, S.models) },
          view.models.map((model) => h(ModelRow, {
            key: model.id, t, lang, model, view, pending,
            accepted: accepted[model.id] === true,
            onAccept: (id, value) => setAccepted((previous) => Object.assign({}, previous, { [id]: value })),
            onAction,
          }))))
    }
    return h('section', { style: S.status, 'aria-label': t('modelsTitle') },
      h('div', { style: S.statusHead },
        h('h4', { style: S.title }, t('modelsTitle')),
        h(SmallButton, { onClick: () => load.current() }, t('refresh'))),
      h('p', { style: S.hint }, t('modelsIntro'), ' ',
        h('a', { href: view ? view.link : 'https://github.com/N0rmal-114/feiyu-memgate#部署档位', target: '_blank', rel: 'noreferrer noopener', style: S.link }, t('modelsLink'))),
      body)
  }

  function SettingsBody(props) {
    const { t, form } = props
    const [draft, setDraft] = useState({})
    const [phase, setPhase] = useState('idle') // idle | saving | saved | failed
    const fetchImpl = props.fetch || ((...args) => fetch(...args))
    const status = useStatus(fetchImpl)
    const state = form && form.state
    // The model list does not depend on the settings form: it shows even while the form is unavailable.
    const models = h(ModelsSection, { t, fetch: fetchImpl, onChange: status.refreshSoon })
    if (!state || state.status === 'unavailable') return h('div', { style: S.page }, h('p', { style: S.note, role: 'status' }, t('unavailable')), models)
    if (state.status !== 'ready') return h('div', { style: S.page }, h('p', { style: S.note, role: 'status' }, t('loading')), models)
    const secrets = status.data && status.data.secrets
    const groups = FIELDS.map((group) => ({ group, views: group.fields.map((field) => fieldView(field, group.group, state, draft, secrets)) }))
    const dirty = buildOps(draft).length > 0
    const invalid = groups.some((g) => g.views.some((v) => v.invalid))
    const writable = state.writable !== false
    const disabled = !writable || phase === 'saving'
    const edit = (k, value) => {
      setPhase('idle')
      setDraft((previous) => Object.assign({}, previous, { [k]: { value } }))
    }
    const reset = (k) => {
      setPhase('idle')
      setDraft((previous) => Object.assign({}, previous, { [k]: { unset: true } }))
    }
    const save = () => {
      if (!dirty || invalid || disabled) return
      setPhase('saving')
      saveDraft(form, draft).then((accepted) => {
        if (accepted) {
          setDraft({})
          setPhase('saved')
          status.refreshSoon()
        } else setPhase('failed')
      }, () => setPhase('failed'))
    }
    const sections = groups.map((g) => h('section', { key: g.group.group, style: S.section, 'aria-label': t('g.' + g.group.group) },
      h('h4', { style: S.sectionTitle }, t('g.' + g.group.group)),
      h('p', { style: S.hint }, t('gh.' + g.group.group)),
      g.views.map((view) => h(Field, { key: view.k, t, view, disabled, edit, reset }))))
    const saved = phase === 'saved' && !dirty ? h('p', { style: S.note, role: 'status' }, t('saved')) : null
    let form_
    if (ui.SettingsForm) {
      form_ = h(ui.SettingsForm, {
        labels: { unavailable: t('unavailable'), readOnly: t('readOnly'), saveFailed: t('saveFailed'), save: t('save'), saving: t('saving') },
        state: { available: true, writable, dirty, invalid, saving: phase === 'saving', failed: phase === 'failed' },
        onSave: save,
        onDiscard: () => setDraft({}),
      }, sections, saved)
    } else {
      form_ = h('div', null,
        writable ? null : h('p', { style: S.note, role: 'status' }, t('readOnly')),
        sections,
        h('div', { style: S.footer },
          phase === 'failed' ? h('p', { style: S.invalid, role: 'status' }, t('saveFailed')) : saved,
          h('button', { type: 'button', style: S.button, disabled: !dirty || invalid || disabled, onClick: save }, phase === 'saving' ? t('saving') : t('save'))))
    }
    return h('div', { style: S.page }, h(StatusBlock, { t, status }), models, form_)
  }

  /** The slot entry: the one-liner for `summary`, the page for `page`. */
  function SettingsPage(props) {
    const t = typeof props.t === 'function' ? props.t : typeof props.translate === 'function' ? props.translate : (key) => key
    if (props.view === 'summary') return t('summary')
    return h(SettingsBody, { t, form: props.form })
  }

  const name = 'dsh-feiyu-memgate'
  const inject = ['slots', 'locale']

  function apply(ctx) {
    ctx.effect(() => ctx.locale.register(NS, dictionaries()), 'dsh-feiyu-memgate: dictionaries')
    const t = ctx.locale.bind(NS)
    ctx.slots.inject('plugins.row.config', () => ctx.slots.register({
      name: 'plugins.row.config',
      key: ROW_KEY,
      locale: NS,
      inject: () => ({ translate: t }),
    }, SettingsPage))
  }

  module.exports = {
    name,
    inject,
    apply,
    // For tests: the page's pure parts and components.
    internals: {
      FIELDS, NS, ROW_KEY, STATUS_ROUTE, MODELS_ROUTE, dictionaries, fieldView, buildOps, saveDraft, statusRows, checkText,
      fetchStatus, fetchModels, postModels, pickText, formatSize, memoryText, modelActions, chainLine, sameProblem, SettingsPage, SettingsBody, ModelsSection,
      ModelRowForTests: ModelRow,
    },
  }
  return module.exports
} })
