# Sentiment–Price Concordance Across News and Social Forums
### A Methodology for Measuring How Well Sentiment Anticipates Asset Value

**Principal investigator:** Gal Av-Gay (independent researcher)
**Project type:** Personal, non-commercial quantitative research

---

## 1. Introduction

The idea that investor sentiment moves asset prices has a long history in financial
economics, and it rests on a theoretical foundation in behavioral finance. De Long,
Shleifer, Summers, and Waldmann showed that irrational "noise traders" whose beliefs
fluctuate can create a risk that deters rational arbitrageurs from trading against
them, so that prices may diverge from fundamentals and remain there.¹ Building on
this, Baker and Wurgler constructed a composite index of investor sentiment and found
that it has its largest effects precisely on stocks that are hard to value and hard
to arbitrage, small, young, volatile, unprofitable, and extreme in growth, with high
sentiment predicting relatively low subsequent returns for exactly those stocks.²,³
Sentiment, in this view, is not noise to be dismissed but a measurable force with
regular and predictable effects.

Empirical work has approached the measurement of sentiment through three broad
channels. The first is the tone of the news media. Tetlock, analyzing the language of
a daily Wall Street Journal column, found that pessimistic media content predicts
downward pressure on prices followed by a reversion toward fundamentals, a pattern
more consistent with sentiment and liquidity effects than with genuinely new
information.⁴ In related work, the fraction of negative words in firm-specific news
was shown to forecast low earnings and short-lived price underreaction,⁵ and García
demonstrated that the predictive power of news tone is concentrated in recessions.⁶
This news-based strand also motivated the development of finance-specific sentiment
dictionaries, since general-purpose word lists misclassify much of the language used
in financial text.⁷

The second channel is survey and polling data, the traditional non-textual measure
of sentiment. Fisher and Statman found that consumer-confidence measures move with
markets but that low confidence is often followed by high subsequent returns, a
contrarian relationship.⁸ Lemmon and Portniaguina showed that the sentiment component
of consumer confidence forecasts the returns of small and low-institutional-ownership
stocks in the manner predicted by noise-trader models,⁹ and Brown and Cliff found
that survey-based sentiment correlates strongly with contemporaneous returns while
having little power to predict them in the near term.¹⁰ These survey measures matter
here because they establish that the sentiment-price relationship is not an artifact
of online text; it appears in deliberately sampled opinion as well.

The third channel, and the most recent, is social media and online forums.
Antweiler and Frank analyzed more than a million stock-message-board posts and found
that message activity helps predict volatility, with a statistically significant but
economically small effect on returns.¹¹ Chen and coauthors showed that the sentiment
of Seeking Alpha articles and their reader commentary predicts returns and earnings
surprises,¹² and Cookson and Niessner used millions of StockTwits messages to
decompose investor disagreement into differences of information and of
interpretation.¹³ The most famous claim in this literature, that Twitter mood
predicts the stock market,¹⁴ has been influential but is methodologically contested;
an out-of-sample replication failed to reproduce it, a caution that is consistent
with data-snooping and that this project takes seriously.¹⁵

A question that cuts across all three channels is one of timing, and it is central to
this study. If sentiment carries information, when does the price actually move in
response, and can an ordinary trader act on it? The market microstructure literature
offers a sobering answer. Barclay and Hendershott found that although after-hours
trading volume is small, after-hours trades are disproportionately informative and
account for meaningful price discovery.¹⁶ Jiang, Likitapiwat, and McInish showed that
following after-hours earnings releases a large share of the eventual price change
occurs immediately, in prices that are already highly efficient before the regular
session opens.¹⁷ If sentiment expressed overnight or before the open is impounded by
extended-hours trading, then a signal that appears to lead the regular-session price
may in fact have been anticipated already, leaving no room for a retail trader who is
active only during regular hours to profit from it. Because a large share of the
sentiment this project collects is in fact expressed outside of regular trading
hours, this timing question is not incidental but a primary object of study. Whether
sentiment anticipates price or merely reacts to it also bears directly on the
efficient-markets hypothesis.¹⁸

## 2. Research Questions

This project is organized around four questions.

1. **Concordance.** To what extent does sentiment toward an asset correlate with that
   asset's value at all?
2. **Direction and lead/lag.** When sentiment and value move together, does the
   sentiment shift precede the value move, which would make it anticipatory, or
   follow it, which would make it reactive? The relationship is measured without
   assuming a direction, so that positive sentiment preceding a decline is as
   informative as positive sentiment preceding a rise.
3. **Speed of response, and the retail-actionability problem.** If an asset's value
   responds to sentiment, how quickly does it do so, and in which market session?
   In particular, does sentiment appear to lead the regular-session price only
   because the extended-hours market has already moved before the regular session
   opens, so that the signal was never actionable for a regular-hours trader?
4. **Source and asset comparison.** Which categories of asset, and which categories
   of mention, whether news or social forum, have sentiment that most accurately and
   most quickly anticipates future value, if any?

## 3. Methodology

### 3.1 Unit of Measurement

The atomic unit of sentiment data is a **mention**, a single dated occurrence of an
asset ticker in a public source. Each mention record captures a small, fixed set of
fields: the exact publication timestamp, normalized to UTC ISO-8601; the source, that
is the platform on which it appeared, such as StockTwits, a named news outlet, or
Reddit; the market session in which the timestamp falls, whether pre-market, regular
hours, after-hours, overnight, a weekend, or a holiday, determined from an
authoritative exchange calendar that accounts for holidays and early closes; a short
context blurb consisting of the sentences that actually name the asset, from which the
sentiment score is computed as described in §3.6 and §3.7; a record of whether the asset
was matched by its ticker symbol, its company name, or both; and a sentiment score
computed deterministically at ingest.

### 3.2 Time Frames of Data Collection

Collection is forward-accumulating rather than a bulk historical scrape. On each run,
only recent, publicly available posts are gathered from a rolling window of the most
recent activity, and no attempt is made to reconstruct dense historical archives.
Runs occur roughly weekly, or more frequently, and over time this assembles a genuine
longitudinal dataset while staying within free-tier rate limits, because each run
requests only what is freely available at that moment. Prices are collected in
parallel over a long trailing window for daily data and, where intraday bars are
available, over roughly the most recent sixty days.

### 3.3 Uniform Sampling for High-Volume Assets

A naive collection strategy would introduce a subtle but serious sampling bias. For
heavily-discussed assets a single request returns only the most recent handful of
messages, sometimes only the last few minutes of activity, so simply keeping the
newest messages on each run would weight the sample toward whatever was said just
before the run and would miss the middle of the interval between runs entirely. The
collector avoids this with a gap-aware, time-uniform sampling scheme. It records the
timestamp of the newest message it had already stored for each asset, which marks the
start of the uncovered gap, and then pages backward through the source until it
reaches that timestamp, so that the entire interval since the last run is traversed
rather than only its final moments. As it does so it bins messages by clock hour and
retains at most a fixed number of messages per hourly bin. Because the cap is applied
uniformly across hours, a high-volume hour is downsampled to a representative subset
while a quiet hour, including overnight and weekend hours, keeps whatever few messages
it genuinely contains, and an hour with no messages remains legitimately empty rather
than being treated as missing data. The result is a sample that is approximately
uniform in time and representative of the whole interval. This matters because
sentiment volume itself spikes around price-moving events, so a recency-weighted or
volume-weighted sample could manufacture a correlation with price that a time-uniform
sample does not.

### 3.4 Incremental, Deduplicated Collection

Because collection accumulates across many runs, it is essential that the same post is
never recorded twice. Each mention is assigned a stable unique identifier derived from
its source and native post identifier, and any identifier seen on a previous run is
skipped. Repeated collection therefore extends the record forward in time without
double-counting, and the dataset grows monotonically and cheaply rather than through
any single intensive scrape.

### 3.5 Two Independent Datasets

The sentiment data and the price data are stored as two independent series and are
aligned only at the point of analysis. The sentiment series is built by aggregating
dated mentions into a per-asset time series and smoothing it with an
exponentially-weighted moving average that has a fourteen-day half-life, so that
recent sentiment dominates while older sentiment decays gradually; both the level of
sentiment and its rate of change are retained. The price series is collected in
parallel and entirely separately. For each asset the daily closing price is recorded
over a long trailing window, and where intraday bars are available an hourly price
series is recorded as well, covering pre-market, regular, and after-hours trading,
with each bar tagged by the session it belongs to. The two datasets are never coupled
at storage time. Anchoring price movements to individual mentions would pre-impose a
correlation structure on the raw data, and would duplicate price observations across
overlapping mentions, biasing any relationship the analysis later reports. Keeping the
series independent means that any genuine relationship must be recovered from
separately-sourced data rather than built in by construction.

### 3.6 Deterministic, Non-LLM Sentiment Scoring

Sentiment is scored using the Loughran-McDonald finance sentiment lexicon,⁷ a
dictionary of positive and negative terms derived from the language of financial
disclosures, augmented with negation handling and hand-coded social-media cues such
as emoji and emphasis, in the spirit of rule-based social-media scorers.¹⁹ The scorer
is fully deterministic: identical text always yields an identical score, on a
continuous scale from −1 for maximally negative to +1 for maximally positive. This
property matters for the measurement. Correlation over time requires a reproducible,
fixed instrument, and a stochastic model such as a large language model, whose
judgments vary between calls, would inject noise into the very quantity being
measured. A finance-specific lexicon is used rather than a general-purpose one because
words such as "liability," "crude," or "aggressive" carry a different valence in a
financial context than in ordinary language.⁷

### 3.7 Relevance Filtering and Entity-Scoped Attribution

A dictionary scorer measures the tone of whatever text it is given, so the text it is
given must genuinely concern the asset in question, and the tone measured must be the
tone directed at that asset rather than at some other company mentioned alongside it.
Two problems arise in practice. First, aggregator feeds often list an article under
every ticker they judge related to it, so a story about one company is delivered under
the symbol of another that it never actually names. Second, a single article can
discuss several companies at once, for example when reporting an acquisition, and a
naive scorer would attribute the tone of the whole passage to each company mentioned,
conflating sentiment about one firm with sentiment about another. Both problems inflate
the mention count with material that is not about the target and corrupt its measured
sentiment with tone borrowed from unrelated firms.

The pipeline addresses both with a single deterministic rule applied before scoring.
The company name for each ticker is resolved once and cached, and the text of a
candidate mention is split into sentences. Only the sentences that name the target,
either by its ticker symbol or by its company name, are retained. If no sentence names
the target, the item is not a genuine mention of that asset and is discarded. If at
least one sentence does, the sentiment score is computed over only those sentences, so
that tone expressed about a co-mentioned company cannot leak onto the target. This
sentence-level scoping keeps the instrument deterministic and auditable while ensuring
that each score reflects only language actually directed at the asset it is attributed
to. The raw text of every retained mention is preserved, and a public per-asset table
exposes it, so that the scoring of any individual mention can be inspected and
verified.

### 3.8 Analysis Plan

Because the two series are independent, they can be aligned at analysis time without
circularity. The primary method is a cross-correlation that relates the sentiment
series, in both its level and its rate of change, to future price returns across a
range of time offsets, which reveals whether sentiment tends to lead or lag price and
by how long, and so speaks directly to the questions of causal direction and speed.
This correlation is computed against two constructions of the price path: the full
path, which includes extended-hours moves, and a regular-hours-only path, which
reflects what a trader active only during the regular session would experience. A
sentiment shift that leads the regular-hours path but merely coincides with, or lags,
the full path is evidence that the extended-hours market had already absorbed it,
which would mean the signal was not actionable for a retail trader by the time the
regular session opened.

Rather than declaring discrete events by an arbitrary threshold, the continuous
sentiment signal is retained in full. Once sufficient data has accumulated, logistic
regression and ROC/AUC analysis²⁰ are used to learn what magnitude of sentiment
change, if any, discriminates subsequent up-moves from down-moves, so that the
threshold becomes an output of the analysis rather than an arbitrary input.
Aggregating these results across many assets and both source types yields the study's
headline output: which asset classes and which mention sources have sentiment that
most accurately, and most quickly, anticipates future value.

### 3.9 Controlling for Earnings Announcements

Earnings announcements are an exogenous information shock: the price moves in response
to the fundamental content of the report, not in response to sentiment, and the two can
be confounded because the report itself generates a burst of mentions at the same
moment the price gaps. Left uncontrolled, this would inflate any apparent
sentiment-price relationship, because both series would be responding to the same
release. The confound is especially acute for this study, because most reports are
issued outside regular trading hours, so their price effect appears precisely in the
extended-hours window that the analysis in §3.8 examines, and after-hours trading is
known to impound the information in an earnings announcement rapidly.¹⁶,¹⁷

This study is not an analysis of earnings. It does not attempt to model or predict them.
It simply notes when each report occurs, using an independently collected calendar of
announcement dates, and holds the affected windows aside so that the sentiment-price
relationship can be read cleanly. Because the price reaction to a report is effectively
immediate rather than spread over days, each announcement is treated as contaminating a
single window that runs from the moment of the report to the next regular market open,
the interval that contains the extended-hours gap and the immediate reaction, and no
longer. The cross-correlation of §3.8 is then computed three ways: over all data, over
data with these earnings windows excluded, and over the earnings windows alone.
Comparing the first two isolates how much of any measured relationship is attributable
to earnings rather than to sentiment tracking price on its own; a relationship that
survives the exclusion is the more credible one. As with the rest of the study, the
earnings calendar is stored independently and aligned only at analysis time, so it
functions purely as a control and is never allowed to manufacture the relationship it is
meant to test.

## 4. Data Handling and Ethics

Only public posts are accessed, and no private data or personally identifying
information is collected. The data is used solely for aggregate statistical analysis,
and individual posts are not republished. Collection volume is minimal, limited to
recent posts gathered roughly weekly, and stays well within free API and feed limits.
The project is non-commercial and exists for personal research and learning.

This document describes the methodology of a working data pipeline. The mention
collector, the entity-scoped relevance filter and sentence-level attribution, the
deterministic Loughran-McDonald scorer, the independent price store, the
market-session classification, the independent earnings calendar and its regime-based
control, and the time-series alignment components are implemented and operational. A
public per-asset table exposes the raw mentions behind each sentiment series for
inspection. Collection currently runs on StockTwits and news sources, and Reddit is
included in the design and will be added once API access is granted.

---

## 5. Bibliography

1. De Long JB, Shleifer A, Summers LH, Waldmann RJ. Noise trader risk in financial markets. J Polit Econ. 1990;98(4):703–38.
2. Baker M, Wurgler J. Investor sentiment and the cross-section of stock returns. J Finance. 2006;61(4):1645–80.
3. Baker M, Wurgler J. Investor sentiment in the stock market. J Econ Perspect. 2007;21(2):129–52.
4. Tetlock PC. Giving content to investor sentiment: the role of media in the stock market. J Finance. 2007;62(3):1139–68.
5. Tetlock PC, Saar-Tsechansky M, Macskassy S. More than words: quantifying language to measure firms' fundamentals. J Finance. 2008;63(3):1437–67.
6. García D. Sentiment during recessions. J Finance. 2013;68(3):1267–300.
7. Loughran T, McDonald B. When is a liability not a liability? Textual analysis, dictionaries, and 10-Ks. J Finance. 2011;66(1):35–65.
8. Fisher KL, Statman M. Consumer confidence and stock returns. J Portf Manag. 2003;30(1):115–27.
9. Lemmon M, Portniaguina E. Consumer confidence and asset prices: some empirical evidence. Rev Financ Stud. 2006;19(4):1499–529.
10. Brown GW, Cliff MT. Investor sentiment and the near-term stock market. J Empir Finance. 2004;11(1):1–27.
11. Antweiler W, Frank MZ. Is all that talk just noise? The information content of internet stock message boards. J Finance. 2004;59(3):1259–94.
12. Chen H, De P, Hu YJ, Hwang B-H. Wisdom of crowds: the value of stock opinions transmitted through social media. Rev Financ Stud. 2014;27(5):1367–403.
13. Cookson JA, Niessner M. Why don't we agree? Evidence from a social network of investors. J Finance. 2020;75(1):173–228.
14. Bollen J, Mao H, Zeng X. Twitter mood predicts the stock market. J Comput Sci. 2011;2(1):1–8.
15. Lachanski M, Pav S. Shy of the character limit: "Twitter mood predicts the stock market" revisited. Econ J Watch. 2017;14(3):302–45.
16. Barclay MJ, Hendershott T. Price discovery and trading after hours. Rev Financ Stud. 2003;16(4):1041–73.
17. Jiang CX, Likitapiwat T, McInish TH. Information content of earnings announcements: evidence from after-hours trading. J Financ Quant Anal. 2012;47(6):1303–30.
18. Fama EF. Efficient capital markets: a review of theory and empirical work. J Finance. 1970;25(2):383–417.
19. Hutto CJ, Gilbert E. VADER: a parsimonious rule-based model for sentiment analysis of social media text. In: Proceedings of the Eighth International AAAI Conference on Weblogs and Social Media (ICWSM); 2014. p. 216–25.
20. Fawcett T. An introduction to ROC analysis. Pattern Recognit Lett. 2006;27(8):861–74.
