# Legal & Compliance

> **This is not legal advice.** Cold email is legal in many countries for business-to-business outreach *if you follow
> the rules*, and restricted or illegal in others. Check the rules where your recipients are, and ask a lawyer if unsure.

## What the pipeline does for you

| Requirement | Built in |
|---|---|
| Identify yourself truthfully | Real name and from-address from your profile; no fake "Re:"/"Fwd:" first emails (blocked) |
| No deceptive subject lines | Misleading-subject check; subjects describe the email |
| Physical postal address | `profile.physical_address` added to every outreach email (`compliance.include_address`) |
| Easy opt-out | Opt-out line in every outreach email + `List-Unsubscribe` header (mailto, and one-click link if `public_base_url` is set) |
| Honour opt-outs promptly | "unsubscribe / stop / remove me / not interested" replies are detected automatically → address suppressed forever, all queued mail cancelled |
| Do-not-contact list | Suppression by email or whole `@domain`; checked again at the moment of sending |
| Only business contacts, relevant offer | Leads come from public business listings/websites; you choose targets |
| Data minimisation | Only business name, public website facts, name/role/business email are stored |
| Right to erasure | `leadpipe forget email@x.com` deletes everything, keeping only a one-way hash so they're never re-added |
| Volume limits | Daily cap, warm-up, max 2 people per company, one company per day |
| Restricted countries | `compliance.restricted_countries: ["DE", "CA"]` disqualifies leads in those countries |

## Country notes (B2B cold email)

| Region | Main law | In short |
|---|---|---|
| **USA** | CAN-SPAM | Allowed (opt-out model). Need: accurate headers, non-deceptive subject, identify as an ad/offer where relevant, **postal address**, working opt-out honoured within 10 business days. |
| **India** | IT Act; **DPDP Act 2023** (rules being phased in); TRAI rules cover calls/SMS, not email | B2B email to business addresses is common. Under DPDP, personal data needs a lawful basis; keep data minimal, honour erasure and opt-out requests promptly, don't buy shady lists. |
| **UK** | PECR + UK GDPR | Emailing **corporate** subscribers (limited companies, LLPs) is allowed with an opt-out. Sole traders and some partnerships are treated as individuals and need prior consent. Use legitimate interest; offer opt-out in every message. |
| **EU** | GDPR + national ePrivacy laws | Varies by country. Many allow B2B cold email under legitimate interest with a clear opt-out and relevant offer; **Germany** (UWG) and some others effectively require prior consent: consider adding them to `restricted_countries`. Tell people where you got their data if asked (state "found on your website/listing"). |
| **Canada** | CASL | Strict. Allowed only with consent; "implied consent" exists for addresses **conspicuously published** for business purposes when your message is relevant to their role, and no "no unsolicited email" notice is shown. Must include identity, address and unsubscribe. Penalties are high: when in doubt, restrict `CA`. |
| **Australia** | Spam Act 2003 | Requires consent; inferred consent can apply to conspicuously published business addresses with a relevant offer. Identify yourself, include unsubscribe. |
| **UAE / Gulf** | Various | Generally permissive for B2B; keep opt-out and honesty rules anyway. |

## Good practice checklist

- [ ] `profile.physical_address` is a real address
- [ ] Sending from a separate domain, with SPF, DKIM, DMARC
- [ ] `restricted_countries` set for places you shouldn't email
- [ ] Opt-out line kept in `compliance.opt_out_line`
- [ ] Never import purchased lists of unknown origin
- [ ] Respond to "where did you get my data?" honestly (public website/listing)
- [ ] Process erasure requests with `leadpipe forget`
- [ ] Keep `data/leadpipe.db` secure and backed up (it contains contact data)

## Platforms

Do **not** automate LinkedIn, Instagram or WhatsApp messaging. It breaks their terms of service and gets accounts
banned. The pipeline only *drafts* those messages (Manual DMs page) for you to send by hand.

Using OpenStreetMap data: © OpenStreetMap contributors (ODbL). Public Nominatim/Overpass servers have fair-use policies;
the pipeline sends one request at a time, identifies itself, and only searches when the lead queue is low.
