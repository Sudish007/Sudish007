# Mermaid theme test

Temporary research fixture for profile README v2. The implementation step removes this file.

## A. Opaque dark context

```mermaid
%%{init: {"theme":"base","themeVariables":{"background":"#07070d","fontFamily":"Inter, Arial, sans-serif","primaryColor":"#101218","primaryTextColor":"#f5f5f2","primaryBorderColor":"#06b6d4","secondaryColor":"#07070d","secondaryTextColor":"#f5f5f2","secondaryBorderColor":"#727988","tertiaryColor":"#101218","tertiaryTextColor":"#f5f5f2","tertiaryBorderColor":"#727988","lineColor":"#727988","textColor":"#f5f5f2","mainBkg":"#101218","nodeBorder":"#06b6d4","clusterBkg":"#07070d","clusterBorder":"#727988","edgeLabelBackground":"#07070d","titleColor":"#f5f5f2"}}}%%
flowchart LR
  sources[Verified sources] --> action[Repository Action]
  action --> cards[Static SVG cards]
  cards --> readme[Profile README]
```

## B. Opaque light context

```mermaid
%%{init: {"theme":"base","themeVariables":{"background":"#f6f7fb","fontFamily":"Inter, Arial, sans-serif","primaryColor":"#f5f5f2","primaryTextColor":"#101218","primaryBorderColor":"#0e7490","secondaryColor":"#f6f7fb","secondaryTextColor":"#101218","secondaryBorderColor":"#687080","tertiaryColor":"#f5f5f2","tertiaryTextColor":"#101218","tertiaryBorderColor":"#687080","lineColor":"#4b5063","textColor":"#101218","mainBkg":"#f5f5f2","nodeBorder":"#0e7490","clusterBkg":"#f6f7fb","clusterBorder":"#687080","edgeLabelBackground":"#f6f7fb","titleColor":"#101218"}}}%%
flowchart LR
  products[Live products] --> checks[HTTP checks]
  checks --> board[Status board]
  board --> readme[Profile README]
```

## C. Transparent canvas with opaque dark nodes

```mermaid
%%{init: {"theme":"base","look":"classic","themeVariables":{"background":"transparent","fontFamily":"Inter, Arial, sans-serif","primaryColor":"#101218","primaryTextColor":"#f5f5f2","primaryBorderColor":"#06b6d4","secondaryColor":"#101218","secondaryTextColor":"#f5f5f2","secondaryBorderColor":"#06b6d4","tertiaryColor":"#101218","tertiaryTextColor":"#f5f5f2","tertiaryBorderColor":"#06b6d4","lineColor":"#727988","textColor":"#f5f5f2","mainBkg":"#101218","nodeBorder":"#06b6d4","clusterBkg":"transparent","clusterBorder":"#727988","edgeLabelBackground":"#101218","titleColor":"#f5f5f2"}}}%%
flowchart LR
  graphql[GitHub GraphQL] --> generator[Card generator]
  generator --> light[Light SVG]
  generator --> dark[Dark SVG]
```
