#include "selfdrive/ui/qt/widgets/ssh_keys.h"

#include "selfdrive/common/params.h"
#include "selfdrive/ui/qt/api.h"
#include "selfdrive/ui/qt/widgets/input.h"

//SshControl::SshControl() : ButtonControl("SSH Keys", "", "Warning: This grants SSH access to all public keys in your GitHub settings. Never enter a GitHub username other than your own. A comma employee will NEVER ask you to add their GitHub username.") {
SshControl::SshControl() : ButtonControl("SSH Keys", "", "Github 사용자 ID에 등록된 SSH키로 변경합니다.") {
  username_label.setAlignment(Qt::AlignRight | Qt::AlignVCenter);
  username_label.setStyleSheet("color: #e0e879");
  hlayout->insertWidget(1, &username_label);

  QObject::connect(this, &ButtonControl::clicked, [=]() {
    //if (text() == "ADD") {
      //QString username = InputDialog::getText("Enter your GitHub username", this);
    if (text() == "개인키사용") {
      QString username = InputDialog::getText("GitHub ID를 입력하세요", this);
      if (username.length() > 0) {
        //setText("LOADING");
        setText("로딩중");
        setEnabled(false);
        getUserKeys(username);
      }
    } else {
      params.remove("GithubUsername");
      params.remove("GithubSshKeys");
      refresh();
    }
  });

  refresh();
}

void SshControl::refresh() {
  QString param = QString::fromStdString(params.get("GithubSshKeys"));
  if (param.length()) {
    username_label.setText(QString::fromStdString(params.get("GithubUsername")));
    //setText("REMOVE");
    setText("개인키제거");
  } else {
    username_label.setText("");
    //setText("ADD");
    setText("개인키사용");
  }
  setEnabled(true);
}

void SshControl::getUserKeys(const QString &username) {
  HttpRequest *request = new HttpRequest(this, false);
  QObject::connect(request, &HttpRequest::requestDone, [=](const QString &resp, bool success) {
    if (success) {
      if (!resp.isEmpty()) {
        params.put("GithubUsername", username.toStdString());
        params.put("GithubSshKeys", resp.toStdString());
      } else {
        //ConfirmationDialog::alert(QString("Username '%1' has no keys on GitHub").arg(username), this);
        ConfirmationDialog::alert(username + "등록된 SSH키가 없습니다.", this);
      }
    } else {
      if (request->timeout()) {
        //ConfirmationDialog::alert("Request timed out", this);
        ConfirmationDialog::alert("요청시간이 초과되었습니다.", this);
      } else {
        //ConfirmationDialog::alert(QString("Username '%1' doesn't exist on GitHub").arg(username), this);
        ConfirmationDialog::alert(username + "등록된 사용자가 아닙니다.", this);
      }
    }

    refresh();
    request->deleteLater();
  });

  request->sendRequest("https://github.com/" + username + ".keys");
}
